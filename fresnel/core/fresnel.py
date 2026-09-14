"""Geometría de la primera zona de Fresnel, curvatura terrestre y veredicto de despeje.

Módulo puro: sin red, sin dependencias de la interfaz. Implementa exactamente
la sección 4 de `Contexto/PROYECTO-FRESNEL.md`.

IMPORTANTE — duplicación intencional: las fórmulas de `radio_fresnel` y
`abultamiento_curvatura` se reimplementan también en JavaScript, en
`web/fresnel.js`, porque el slider de frecuencia debe recalcular y redibujar
en el navegador sin volver a llamar al backend (ver Contexto/CLAUDE.md,
decisión 1-2). Si se modifica una fórmula aquí, hay que reflejar el mismo
cambio en `web/fresnel.js`; el test `tests/test_consistencia_js_py.py` está
para detectar la divergencia si alguna de las dos copias se desactualiza.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import sqrt
from typing import List

VELOCIDAD_LUZ_M_S = 299_792_458.0
"""Velocidad de la luz en el vacío. Se usa como aproximación de la velocidad
de propagación de la onda de radio en el aire (el índice de refracción del
aire es ~1.0003, la diferencia es despreciable para este cálculo)."""

K_ESTANDAR = 4.0 / 3.0
"""Factor de radio terrestre efectivo para atmósfera estándar. La atmósfera
refracta la onda de radio ligeramente hacia la Tierra, lo que equivale a
tratar la Tierra como si tuviera un radio 4/3 mayor al real para poder seguir
dibujando la línea de vista como una recta. En climas con humedad y
gradientes térmicos distintos (como el trópico húmedo colombiano) k puede
desviarse de este valor, por eso queda editable."""

CRITERIO_DESPEJE_ESTANDAR_PCT = 60.0
"""Porcentaje mínimo del radio de F1 que debe quedar libre de obstáculos,
según la recomendación ITU-R P.530, para que la pérdida por difracción sea
despreciable."""

_RADIO_MINIMO_SIGNIFICATIVO_M = 1e-6
"""Umbral por debajo del cual el radio de F1 se considera cero (en las
antenas mismas, donde la elipse de Fresnel converge a un punto). Evita
dividir por cero al calcular el porcentaje de despeje en esos puntos."""


def radio_fresnel(d1_m: float, d2_m: float, frecuencia_hz: float) -> float:
    """Radio de la primera zona de Fresnel en un punto del trayecto, en metros.

    La zona de Fresnel es la región elipsoidal alrededor de la línea de vista
    donde un obstáculo introduce una diferencia de camino óptico de hasta
    media longitud de onda entre el rayo directo y un rayo desviado; más
    allá de esa región la interferencia deja de ser significativa. Su
    sección transversal en cualquier punto del trayecto es un círculo cuyo
    radio depende de qué tan cerca está ese punto de cada antena: el radio
    es máximo en el centro del enlace (d1 = d2) y se cierra a cero en las
    antenas (d1 = 0 o d2 = 0), porque ahí no hay margen físico para que un
    camino alterno diverja del directo.

    Fórmula general en SI (todo en metros):

        r1 = sqrt(lambda * d1 * d2 / (d1 + d2))

    donde `lambda = c / f` es la longitud de onda, `d1` la distancia desde
    la antena A hasta el punto evaluado y `d2` la distancia desde ese punto
    hasta la antena B.
    """
    distancia_total_m = d1_m + d2_m
    if distancia_total_m <= 0:
        return 0.0
    longitud_onda_m = VELOCIDAD_LUZ_M_S / frecuencia_hz
    return sqrt(longitud_onda_m * d1_m * d2_m / distancia_total_m)


def abultamiento_curvatura(d1_m: float, d2_m: float, k: float) -> float:
    """Abultamiento aparente del terreno por la curvatura terrestre, en metros.

    La línea de vista entre dos antenas es una recta en el espacio, pero la
    superficie de la Tierra se curva por debajo de ella. Si se quiere dibujar
    esa línea de vista como una recta horizontal (más simple de graficar y
    de razonar), hay que compensar sumándole a la elevación del terreno,
    en cada punto, cuánto "se levanta" ese punto respecto a la cuerda recta
    que conectaría las bases de las antenas si la Tierra fuera plana.

    El factor `k` ajusta el radio terrestre efectivo para tener en cuenta la
    refracción atmosférica (ver `K_ESTANDAR`): a mayor `k`, la Tierra
    "efectiva" es más grande, la curvatura es menos pronunciada y el
    abultamiento es menor.

    Fórmula práctica (d1, d2 en kilómetros, resultado en metros):

        h_curv = d1_km * d2_km / (12.75 * k)

    El `12.75` sale de `2 * R_tierra / 1000` con R_tierra en metros, para
    dejar la fórmula en función de distancias en km y resultado en metros.
    """
    d1_km = d1_m / 1000.0
    d2_km = d2_m / 1000.0
    return (d1_km * d2_km) / (12.75 * k)


@dataclass(frozen=True)
class MuestraTerreno:
    """Un punto del perfil del trayecto con su elevación sobre el nivel del mar.

    Es la entrada que produce la capa de elevaciones (`core/elevation.py`)
    a partir de los puntos que entrega `core/geo.muestrear_trayecto`.
    """

    latitud: float
    longitud: float
    distancia_acumulada_m: float
    elevacion_msnm: float


class EstadoEnlace(str, Enum):
    """Los tres veredictos posibles para un enlace, en orden de severidad creciente."""

    VIABLE = "VIABLE"
    INVASION_FRESNEL = "INVASION_FRESNEL"
    LOS_BLOQUEADA = "LOS_BLOQUEADA"


@dataclass(frozen=True)
class MuestraAnalisis:
    """Resultado del análisis de Fresnel en un punto específico del trayecto."""

    latitud: float
    longitud: float
    distancia_acumulada_m: float
    radio_f1_m: float
    abultamiento_m: float
    altura_linea_vista_m: float
    holgura_m: float
    despeje_pct: float


@dataclass(frozen=True)
class Veredicto:
    """Diagnóstico global del enlace, determinado por el punto más crítico.

    El punto crítico es la muestra con el menor `despeje_pct`: es el lugar
    del trayecto donde el terreno está proporcionalmente más cerca de
    invadir (o ya invade) la zona de Fresnel, y por lo tanto el que decide
    si el enlace completo es viable.
    """

    estado: EstadoEnlace
    distancia_punto_critico_m: float
    latitud_critica: float
    longitud_critica: float
    despeje_pct_critico: float
    mensaje: str


@dataclass(frozen=True)
class ResultadoAnalisis:
    """Resultado completo del análisis de un enlace: agregados + perfil punto a punto."""

    distancia_total_m: float
    longitud_onda_m: float
    radio_f1_maximo_m: float
    altura_efectiva_a_m: float
    altura_efectiva_b_m: float
    muestras: List[MuestraAnalisis]
    veredicto: Veredicto


def _formato_es(valor: float) -> str:
    """Formatea un número con un decimal y coma decimal (convención en español)."""
    return f"{valor:.1f}".replace(".", ",")


def _construir_mensaje(
    estado: EstadoEnlace,
    distancia_critica_km: float,
    despeje_pct_critico: float,
    criterio_despeje_pct: float,
) -> str:
    dist = _formato_es(distancia_critica_km)
    pct = _formato_es(despeje_pct_critico)
    if estado is EstadoEnlace.VIABLE:
        return f"ENLACE VIABLE — despeje mínimo {pct}% de F1 a {dist} km del punto A"
    if estado is EstadoEnlace.INVASION_FRESNEL:
        criterio = _formato_es(criterio_despeje_pct)
        return (
            f"OBSTRUCCIÓN — el terreno invade la zona de Fresnel a {dist} km "
            f"del punto A; despeje {pct}% (mínimo requerido {criterio}%)"
        )
    return f"LÍNEA DE VISTA BLOQUEADA — el terreno corta la línea directa a {dist} km del punto A"


def analizar_enlace(
    perfil: List[MuestraTerreno],
    elevacion_msnm_a: float,
    altura_torre_a_m: float,
    elevacion_msnm_b: float,
    altura_torre_b_m: float,
    frecuencia_hz: float,
    k: float = K_ESTANDAR,
    criterio_despeje_pct: float = CRITERIO_DESPEJE_ESTANDAR_PCT,
) -> ResultadoAnalisis:
    """Analiza un enlace completo: geometría de Fresnel + veredicto de despeje.

    Recorre cada muestra del perfil de terreno y, en cada una, compara dos
    alturas: la de la línea de vista directa entre antenas (interpolación
    lineal entre las alturas efectivas de A y B, porque la línea de vista es
    literalmente una recta en el espacio) contra la del terreno real,
    corregido por el abultamiento de la curvatura terrestre. La diferencia
    entre ambas (`holgura`) se expresa como porcentaje del radio de F1 en ese
    punto (`despeje_pct`), que es la cantidad físicamente relevante: la
    misma holgura en metros representa una invasión distinta según qué tan
    grande es la zona de Fresnel en ese punto del trayecto.

    En las antenas mismas (d1 = 0 o d2 = 0) el radio de F1 es cero por
    definición — no hay zona que invadir ahí — así que esos puntos no
    participan en la búsqueda del punto crítico; se les asigna despeje
    infinito (positivo si la torre está sobre el terreno, como es siempre el
    caso) para que nunca sean, por sí mismos, el punto que decide el
    veredicto.

    El veredicto global lo determina la muestra de menor `despeje_pct`:
    - `despeje_pct >= criterio_despeje_pct` en el punto crítico -> VIABLE.
    - `0 <= despeje_pct < criterio_despeje_pct` -> INVASION_FRESNEL (el
      terreno entra en el volumen de la elipse de Fresnel, pero la línea de
      vista directa sigue libre).
    - `despeje_pct < 0` -> LOS_BLOQUEADA (el terreno corta la línea de vista
      directa, la condición más severa).
    """
    if len(perfil) < 2:
        raise ValueError("el perfil necesita al menos 2 muestras (A y B)")

    distancia_total_m = perfil[-1].distancia_acumulada_m
    altura_efectiva_a_m = elevacion_msnm_a + altura_torre_a_m
    altura_efectiva_b_m = elevacion_msnm_b + altura_torre_b_m
    longitud_onda_m = VELOCIDAD_LUZ_M_S / frecuencia_hz
    radio_f1_maximo_m = radio_fresnel(
        distancia_total_m / 2.0, distancia_total_m / 2.0, frecuencia_hz
    )

    muestras: List[MuestraAnalisis] = []
    for punto in perfil:
        d1_m = punto.distancia_acumulada_m
        d2_m = distancia_total_m - d1_m

        radio_f1_m = radio_fresnel(d1_m, d2_m, frecuencia_hz)
        abultamiento_m = abultamiento_curvatura(d1_m, d2_m, k)

        fraccion = d1_m / distancia_total_m if distancia_total_m > 0 else 0.0
        altura_linea_vista_m = altura_efectiva_a_m + fraccion * (
            altura_efectiva_b_m - altura_efectiva_a_m
        )

        terreno_efectivo_m = punto.elevacion_msnm + abultamiento_m
        holgura_m = altura_linea_vista_m - terreno_efectivo_m

        if radio_f1_m > _RADIO_MINIMO_SIGNIFICATIVO_M:
            despeje_pct = holgura_m / radio_f1_m * 100.0
        else:
            despeje_pct = float("inf") if holgura_m >= 0 else float("-inf")

        muestras.append(
            MuestraAnalisis(
                latitud=punto.latitud,
                longitud=punto.longitud,
                distancia_acumulada_m=d1_m,
                radio_f1_m=radio_f1_m,
                abultamiento_m=abultamiento_m,
                altura_linea_vista_m=altura_linea_vista_m,
                holgura_m=holgura_m,
                despeje_pct=despeje_pct,
            )
        )

    critica = min(muestras, key=lambda m: m.despeje_pct)

    if critica.despeje_pct < 0:
        estado = EstadoEnlace.LOS_BLOQUEADA
    elif critica.despeje_pct < criterio_despeje_pct:
        estado = EstadoEnlace.INVASION_FRESNEL
    else:
        estado = EstadoEnlace.VIABLE

    mensaje = _construir_mensaje(
        estado,
        critica.distancia_acumulada_m / 1000.0,
        critica.despeje_pct,
        criterio_despeje_pct,
    )

    veredicto = Veredicto(
        estado=estado,
        distancia_punto_critico_m=critica.distancia_acumulada_m,
        latitud_critica=critica.latitud,
        longitud_critica=critica.longitud,
        despeje_pct_critico=critica.despeje_pct,
        mensaje=mensaje,
    )

    return ResultadoAnalisis(
        distancia_total_m=distancia_total_m,
        longitud_onda_m=longitud_onda_m,
        radio_f1_maximo_m=radio_f1_maximo_m,
        altura_efectiva_a_m=altura_efectiva_a_m,
        altura_efectiva_b_m=altura_efectiva_b_m,
        muestras=muestras,
        veredicto=veredicto,
    )
