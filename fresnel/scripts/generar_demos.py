"""Genera los tres casos demo de la sección 7.1 de la especificación.

Para cada categoría (despejado, obstruido, marginal) prueba una lista de
ubicaciones candidatas reales en Bogotá/Cundinamarca, consulta su
elevación real (con caché en disco para no repetir consultas entre
corridas), verifica con `core/fresnel.analizar_enlace` que el candidato
produce el veredicto buscado, y congela el perfil de elevación resultante
en `data/demos/<id>.json` junto con los parámetros del enlace. Ese archivo
queda versionado en el repositorio y no requiere red para reproducirse
(ver `core/elevation.CacheElevacionEnDisco.precargar`, usado por
`api/main.py` al arrancar).

Uso:
    python scripts/generar_demos.py
"""

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.elevation import CacheElevacionEnDisco, OpenTopoData, PuntoConsulta, clave_cache
from core.fresnel import EstadoEnlace, MuestraTerreno, K_ESTANDAR, analizar_enlace
from core.geo import distancia_geodesica, muestrear_trayecto, numero_muestras_recomendado

DIRECTORIO_FRESNEL = Path(__file__).resolve().parent.parent
RUTA_CACHE_ITERACION = DIRECTORIO_FRESNEL / "cache" / "elevaciones.json"
DIRECTORIO_DEMOS = DIRECTORIO_FRESNEL / "data" / "demos"

FRECUENCIA_REFERENCIA_BAJA_HZ = 900e6
FRECUENCIA_REFERENCIA_ALTA_HZ = 5.8e9

fuente_elevacion = CacheElevacionEnDisco(OpenTopoData(), RUTA_CACHE_ITERACION)


@dataclass
class Candidato:
    id: str
    nombre: str
    descripcion_base: str
    lat_a: float
    lon_a: float
    lat_b: float
    lon_b: float
    altura_torre_a_m: float = 20.0
    altura_torre_b_m: float = 20.0
    k: float = K_ESTANDAR
    criterio_despeje_pct: float = 60.0


def _perfil_real(cand: Candidato) -> Tuple[List[MuestraTerreno], List[float]]:
    distancia_m = distancia_geodesica(cand.lat_a, cand.lon_a, cand.lat_b, cand.lon_b)
    n = numero_muestras_recomendado(distancia_m)
    puntos = muestrear_trayecto(cand.lat_a, cand.lon_a, cand.lat_b, cand.lon_b, n)
    elevaciones = fuente_elevacion.consultar(
        [PuntoConsulta(p.latitud, p.longitud) for p in puntos]
    )
    perfil = [
        MuestraTerreno(
            latitud=p.latitud,
            longitud=p.longitud,
            distancia_acumulada_m=p.distancia_acumulada_m,
            elevacion_msnm=e,
        )
        for p, e in zip(puntos, elevaciones)
    ]
    print(
        f"    distancia={distancia_m/1000:.2f} km, muestras={n}, "
        f"elevación A={elevaciones[0]:.0f} msnm, B={elevaciones[-1]:.0f} msnm, "
        f"pico intermedio={max(elevaciones):.0f} msnm"
    )
    return perfil, elevaciones


def _analizar(
    cand: Candidato,
    frecuencia_hz: float,
    perfil: List[MuestraTerreno],
    elevaciones: List[float],
):
    return analizar_enlace(
        perfil,
        elevacion_msnm_a=elevaciones[0],
        altura_torre_a_m=cand.altura_torre_a_m,
        elevacion_msnm_b=elevaciones[-1],
        altura_torre_b_m=cand.altura_torre_b_m,
        frecuencia_hz=frecuencia_hz,
        k=cand.k,
        criterio_despeje_pct=cand.criterio_despeje_pct,
    )


def _buscar_frecuencia_de_cruce(
    cand: Candidato,
    perfil: List[MuestraTerreno],
    elevaciones: List[float],
    f_lo: float = 100e6,
    f_hi: float = 30e9,
) -> Optional[float]:
    """Frecuencia (Hz) donde el veredicto pasa de obstruido a VIABLE.

    El % de despeje crece monótonamente con la frecuencia: a mayor
    frecuencia, menor longitud de onda, menor radio F1, y el mismo
    obstáculo físico representa una fracción menor de una zona más chica.
    Por eso una búsqueda binaria sobre el estado (no viable / viable) es
    válida para encontrar el punto de cruce.
    """

    def es_viable(f: float) -> bool:
        return _analizar(cand, f, perfil, elevaciones).veredicto.estado is EstadoEnlace.VIABLE

    if es_viable(f_lo):
        return None  # ya viable en la banda más baja: no sirve como caso marginal
    if not es_viable(f_hi):
        return None  # sigue obstruido incluso en la banda más alta: no es "marginal"

    for _ in range(40):
        f_mid = (f_lo + f_hi) / 2
        if es_viable(f_mid):
            f_hi = f_mid
        else:
            f_lo = f_mid
    return f_hi


def _elevaciones_a_diccionario(perfil: List[MuestraTerreno]) -> dict:
    return {clave_cache(m.latitud, m.longitud): m.elevacion_msnm for m in perfil}


def _escribir_demo(
    cand: Candidato,
    perfil: List[MuestraTerreno],
    descripcion: str,
    frecuencia_valor_ghz: float,
) -> None:
    datos = {
        "id": cand.id,
        "nombre": cand.nombre,
        "descripcion": descripcion,
        "parametros": {
            "punto_a": {
                "latitud": cand.lat_a,
                "longitud": cand.lon_a,
                "altura_torre_m": cand.altura_torre_a_m,
            },
            "punto_b": {
                "latitud": cand.lat_b,
                "longitud": cand.lon_b,
                "altura_torre_m": cand.altura_torre_b_m,
            },
            "frecuencia_valor": round(frecuencia_valor_ghz, 4),
            "frecuencia_unidad": "GHz",
            "k": cand.k,
            "criterio_despeje_pct": cand.criterio_despeje_pct,
        },
        "elevaciones": _elevaciones_a_diccionario(perfil),
    }
    DIRECTORIO_DEMOS.mkdir(parents=True, exist_ok=True)
    ruta = DIRECTORIO_DEMOS / f"{cand.id}.json"
    ruta.write_text(
        json.dumps(datos, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    print(f"    -> escrito {ruta.relative_to(DIRECTORIO_FRESNEL)} ({len(datos['elevaciones'])} elevaciones)")


# --- Candidatos: ubicaciones reales en Bogotá / Cundinamarca ---

CANDIDATOS_DESPEJADO = [
    Candidato(
        id="despejado",
        nombre="Enlace despejado — Sabana de Bogotá",
        descripcion_base=(
            "Bogotá (Parque Simón Bolívar) - Cota: terreno plano de la sabana, "
            "sin relieve significativo entre los dos puntos."
        ),
        lat_a=4.6588, lon_a=-74.0937,
        lat_b=4.8114, lon_b=-74.1015,
        altura_torre_a_m=40.0,
        altura_torre_b_m=40.0,
    ),
]

CANDIDATOS_OBSTRUIDO = [
    Candidato(
        id="obstruido",
        nombre="Enlace obstruido — cruce de los Cerros Orientales",
        descripcion_base=(
            "Bogotá (La Candelaria) - Choachí: el trayecto directo cruza la "
            "cordillera oriental (Alto de Patios), con picos muy por encima "
            "de la línea de vista de ambos extremos."
        ),
        lat_a=4.5981, lon_a=-74.0761,
        lat_b=4.5333, lon_b=-73.9167,
    ),
]

CANDIDATOS_MARGINAL = [
    Candidato(
        id="marginal",
        nombre="Enlace marginal — Chía / Cajicá",
        descripcion_base=(
            "Chía - Cajicá: el trayecto cruza una loma baja y suave de la "
            "sabana (relieve de apenas ~13 m sobre el promedio de los "
            "extremos), justo del tamaño que la zona de Fresnel invade o no "
            "según la banda."
        ),
        lat_a=4.8600, lon_a=-74.0300,
        lat_b=4.9200, lon_b=-74.0100,
        altura_torre_a_m=20.0,
        altura_torre_b_m=20.0,
    ),
    Candidato(
        id="marginal",
        nombre="Enlace marginal — Bogotá / La Calera",
        descripcion_base=(
            "Bogotá (Chapinero Alto) - La Calera: el trayecto cruza un "
            "contrafuerte moderado de las estribaciones orientales, más bajo "
            "que la cordillera principal."
        ),
        lat_a=4.6520, lon_a=-74.0450,
        lat_b=4.7186, lon_b=-73.9736,
    ),
    Candidato(
        id="marginal",
        nombre="Enlace marginal — Cerro de Suba",
        descripcion_base="Bogotá: el trayecto cruza el Cerro de Suba, una colina moderada dentro de la sabana.",
        lat_a=4.7570, lon_a=-74.1050,
        lat_b=4.7450, lon_b=-74.0650,
    ),
]


def generar_despejado() -> bool:
    print("=== Caso DESPEJADO ===")
    for cand in CANDIDATOS_DESPEJADO:
        print(f"  probando: {cand.nombre}")
        perfil, elevaciones = _perfil_real(cand)
        estados = {
            f: _analizar(cand, f, perfil, elevaciones).veredicto.estado
            for f in (FRECUENCIA_REFERENCIA_BAJA_HZ, FRECUENCIA_REFERENCIA_ALTA_HZ)
        }
        if all(e is EstadoEnlace.VIABLE for e in estados.values()):
            descripcion = (
                f"{cand.descripcion_base} Enlace viable en toda la banda de "
                f"microondas de referencia (verificado entre "
                f"{FRECUENCIA_REFERENCIA_BAJA_HZ/1e6:.0f} MHz y "
                f"{FRECUENCIA_REFERENCIA_ALTA_HZ/1e9:.1f} GHz)."
            )
            _escribir_demo(cand, perfil, descripcion, FRECUENCIA_REFERENCIA_ALTA_HZ / 1e9)
            print("  OK: VIABLE confirmado en todo el rango de referencia.\n")
            return True
        print(f"  rechazado: estados = {[e.value for e in estados.values()]}\n")
    print("  ADVERTENCIA: ningún candidato despejado sirvió.\n")
    return False


def generar_obstruido() -> bool:
    print("=== Caso OBSTRUIDO ===")
    for cand in CANDIDATOS_OBSTRUIDO:
        print(f"  probando: {cand.nombre}")
        perfil, elevaciones = _perfil_real(cand)
        estados = {
            f: _analizar(cand, f, perfil, elevaciones).veredicto.estado
            for f in (FRECUENCIA_REFERENCIA_BAJA_HZ, FRECUENCIA_REFERENCIA_ALTA_HZ, 18e9)
        }
        if all(e is EstadoEnlace.LOS_BLOQUEADA for e in estados.values()):
            descripcion = (
                f"{cand.descripcion_base} Línea de vista bloqueada en toda la "
                "banda de microondas de referencia (verificado entre "
                f"{FRECUENCIA_REFERENCIA_BAJA_HZ/1e6:.0f} MHz y 18 GHz): es la "
                "cordillera misma la que corta la LOS, no una invasión parcial "
                "de la zona de Fresnel, así que ningún cambio de frecuencia lo "
                "resuelve."
            )
            _escribir_demo(cand, perfil, descripcion, FRECUENCIA_REFERENCIA_ALTA_HZ / 1e9)
            print("  OK: LOS_BLOQUEADA confirmado en todo el rango de referencia.\n")
            return True
        print(f"  rechazado: estados = {[e.value for e in estados.values()]}\n")
    print("  ADVERTENCIA: ningún candidato obstruido sirvió.\n")
    return False


def generar_marginal() -> bool:
    print("=== Caso MARGINAL ===")
    for cand in CANDIDATOS_MARGINAL:
        print(f"  probando: {cand.nombre}")
        perfil, elevaciones = _perfil_real(cand)
        cruce_hz = _buscar_frecuencia_de_cruce(cand, perfil, elevaciones)
        if cruce_hz is None:
            estado_baja = _analizar(cand, FRECUENCIA_REFERENCIA_BAJA_HZ, perfil, elevaciones).veredicto.estado
            estado_alta = _analizar(cand, 30e9, perfil, elevaciones).veredicto.estado
            print(f"  rechazado: no cruza a VIABLE en el rango de búsqueda "
                  f"(estado a 100 MHz-equiv.: {estado_baja.value}, a 30 GHz: {estado_alta.value})\n")
            continue

        estado_baja = _analizar(cand, FRECUENCIA_REFERENCIA_BAJA_HZ, perfil, elevaciones).veredicto
        estado_alta = _analizar(cand, FRECUENCIA_REFERENCIA_ALTA_HZ, perfil, elevaciones).veredicto

        print(f"    frecuencia de cruce (obstruido -> VIABLE): {cruce_hz/1e9:.4f} GHz")
        print(f"    a {FRECUENCIA_REFERENCIA_BAJA_HZ/1e9:.1f} GHz: {estado_baja.estado.value} "
              f"(despeje crítico {estado_baja.despeje_pct_critico:.1f}%)")
        print(f"    a {FRECUENCIA_REFERENCIA_ALTA_HZ/1e9:.1f} GHz: {estado_alta.estado.value} "
              f"(despeje crítico {estado_alta.despeje_pct_critico:.1f}%)")

        if estado_baja.estado is EstadoEnlace.VIABLE:
            # la banda baja de referencia ya quedó por encima del cruce; no
            # sirve como demostración en vivo del cambio de veredicto.
            print("  rechazado: la banda baja de referencia ya es VIABLE, no ilustra el cruce.\n")
            continue

        descripcion = (
            f"{cand.descripcion_base} A {FRECUENCIA_REFERENCIA_BAJA_HZ/1e9:.1f} GHz "
            f"el enlace está {estado_baja.estado.value} (despeje crítico "
            f"{estado_baja.despeje_pct_critico:.1f}%); subiendo la frecuencia por "
            f"encima de ~{cruce_hz/1e9:.2f} GHz pasa a VIABLE (a "
            f"{FRECUENCIA_REFERENCIA_ALTA_HZ/1e9:.1f} GHz el despeje crítico es "
            f"{estado_alta.despeje_pct_critico:.1f}%)."
        )
        _escribir_demo(cand, perfil, descripcion, FRECUENCIA_REFERENCIA_BAJA_HZ / 1e9)
        print("  OK: caso marginal confirmado.\n")
        print("  >>> PARA LA SUSTENTACIÓN:")
        print(f"      obstruido en banda baja  : {FRECUENCIA_REFERENCIA_BAJA_HZ/1e9:.1f} GHz "
              f"-> {estado_baja.estado.value}")
        print(f"      viable en banda alta     : {FRECUENCIA_REFERENCIA_ALTA_HZ/1e9:.1f} GHz -> VIABLE")
        print(f"      cruce exacto             : ~{cruce_hz/1e9:.3f} GHz\n")
        return True
    print("  ADVERTENCIA: ningún candidato marginal sirvió; ajustar coordenadas o alturas de torre.\n")
    return False


def main() -> None:
    ok_despejado = generar_despejado()
    ok_obstruido = generar_obstruido()
    ok_marginal = generar_marginal()

    if not (ok_despejado and ok_obstruido and ok_marginal):
        print("Algún caso demo no se pudo generar. Revisa las advertencias arriba.")
        sys.exit(1)
    print("Los tres casos demo se generaron correctamente en data/demos/.")


if __name__ == "__main__":
    main()
