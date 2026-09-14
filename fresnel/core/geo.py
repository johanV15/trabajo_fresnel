"""Geometría del trayecto entre dos antenas.

Módulo puro: sin llamadas de red, sin dependencias de la interfaz. Toda la
geodesia usa el elipsoide WGS-84 (el mismo datum que usan GPS y Google Maps),
vía pyproj, en vez de aproximar la Tierra como una esfera.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from pyproj import Geod

_WGS84 = Geod(ellps="WGS84")


@dataclass(frozen=True)
class PuntoTrayecto:
    """Un punto muestreado sobre la geodésica entre A y B.

    Attributes:
        latitud: grados decimales.
        longitud: grados decimales.
        distancia_acumulada_m: distancia geodésica recorrida desde A hasta
            este punto, en metros. Es 0 en A y la distancia total en B.
    """

    latitud: float
    longitud: float
    distancia_acumulada_m: float


def distancia_geodesica(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia geodésica entre dos puntos sobre el elipsoide WGS-84, en metros.

    No se usa la distancia en línea recta (cuerda) ni una esfera perfecta:
    se usa la distancia real sobre la superficie del elipsoide, calculada
    resolviendo el problema geodésico inverso (algoritmo de Karney). Es la
    misma distancia que reporta un GPS o Google Maps entre dos coordenadas,
    y es la que corresponde físicamente al trayecto que recorre la onda de
    radio siguiendo la curvatura terrestre.
    """
    _, _, distancia_m = _WGS84.inv(lon1, lat1, lon2, lat2)
    return distancia_m


def numero_muestras_recomendado(distancia_m: float) -> int:
    """Número de puntos a muestrear a lo largo del trayecto, acotado entre 200 y 500.

    Un enlace corto no necesita muchos puntos para capturar el relieve, pero
    tampoco conviene bajar de 200 porque se pueden perder picos angostos del
    terreno (un cerro puntual entre las dos antenas). Un enlace largo, en
    cambio, podría pedir miles de puntos si se escalara sin techo, y cada
    punto es una consulta a la API de elevación (con su costo y su rate
    limit) — por eso se limita a 500 muestras como máximo, más allá de las
    cuales no se gana resolución perceptible para este propósito.

    Criterio: ~15 muestras por kilómetro, con piso 200 y techo 500. El piso
    domina en enlaces menores a ~13.3 km; el techo domina a partir de
    ~33.3 km.
    """
    distancia_km = distancia_m / 1000.0
    n = round(distancia_km * 15)
    return max(200, min(500, n))


def muestrear_trayecto(
    lat1: float, lon1: float, lat2: float, lon2: float, n: int
) -> list[PuntoTrayecto]:
    """Muestrea `n` puntos sobre la geodésica entre A y B, incluyendo ambos extremos.

    Los puntos intermedios se calculan sobre la geodésica real (círculo
    máximo del elipsoide), no por interpolación lineal de latitud/longitud.
    La interpolación lineal en lat/long se desvía del trayecto físico real
    en enlaces largos y cerca de los polos, porque los meridianos convergen;
    la geodésica es el camino más corto real sobre la superficie terrestre,
    que es el que sigue (en línea recta, por encima de esa superficie) el
    rayo del enlace de radio.

    Si A y B son el mismo punto (distancia cero), se devuelven `n` copias
    del mismo punto con distancia acumulada 0 — no es un caso de error a
    este nivel, la validación de "A y B son el mismo lugar" es
    responsabilidad de la capa de API.
    """
    if n < 2:
        raise ValueError("se necesitan al menos 2 muestras (A y B)")

    distancia_total_m = distancia_geodesica(lat1, lon1, lat2, lon2)

    if distancia_total_m == 0.0:
        return [
            PuntoTrayecto(latitud=lat1, longitud=lon1, distancia_acumulada_m=0.0)
            for _ in range(n)
        ]

    # Geod.npts() da los n-2 puntos intermedios sobre la geodésica; A y B se
    # agregan aparte para garantizar que los extremos sean exactos y no una
    # aproximación numérica del propio cálculo geodésico.
    intermedios = _WGS84.npts(lon1, lat1, lon2, lat2, n - 2)

    puntos: list[PuntoTrayecto] = [
        PuntoTrayecto(latitud=lat1, longitud=lon1, distancia_acumulada_m=0.0)
    ]
    for lon, lat in intermedios:
        d_desde_a = distancia_geodesica(lat1, lon1, lat, lon)
        puntos.append(
            PuntoTrayecto(latitud=lat, longitud=lon, distancia_acumulada_m=d_desde_a)
        )
    puntos.append(
        PuntoTrayecto(
            latitud=lat2, longitud=lon2, distancia_acumulada_m=distancia_total_m
        )
    )
    return puntos
