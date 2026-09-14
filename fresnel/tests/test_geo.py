"""Tests de core/geo.py: distancia geodésica y muestreo del trayecto."""

import pytest

from core.geo import (
    distancia_geodesica,
    muestrear_trayecto,
    numero_muestras_recomendado,
)


def test_distancia_conocida_un_grado_de_longitud_en_el_ecuador():
    """1 grado de longitud sobre el ecuador en WGS-84 mide ~111.32 km.

    Es un valor de referencia estándar (verificable también en Google
    Maps: (0, 0) -> (0, 1)) que no depende de la propia implementación.
    """
    distancia_m = distancia_geodesica(0.0, 0.0, 0.0, 1.0)
    assert distancia_m == pytest.approx(111_319.49, abs=1.0)


def test_distancia_bogota_medellin_orden_de_magnitud():
    """Bogotá-Medellín es un caso real conocido: ~246 km en línea recta (geodésica)."""
    # Bogotá (Plaza de Bolívar) y Medellín (Plaza Botero), aprox.
    distancia_m = distancia_geodesica(4.5981, -74.0761, 6.2518, -75.5636)
    distancia_km = distancia_m / 1000.0
    assert 240 < distancia_km < 250


def test_distancia_cero_mismo_punto():
    assert distancia_geodesica(4.6, -74.1, 4.6, -74.1) == pytest.approx(0.0, abs=1e-6)


def test_numero_muestras_recomendado_respeta_piso_y_techo():
    assert numero_muestras_recomendado(1_000) == 200  # enlace de 1 km -> piso
    assert numero_muestras_recomendado(50_000) == 500  # enlace de 50 km -> techo
    # 20 km * 15 muestras/km = 300, dentro del rango sin clamping.
    assert numero_muestras_recomendado(20_000) == 300


def test_muestrear_trayecto_extremos_exactos():
    lat1, lon1 = 4.5981, -74.0761
    lat2, lon2 = 6.2518, -75.5636
    puntos = muestrear_trayecto(lat1, lon1, lat2, lon2, 250)

    assert puntos[0].latitud == lat1
    assert puntos[0].longitud == lon1
    assert puntos[0].distancia_acumulada_m == pytest.approx(0.0, abs=1e-9)

    assert puntos[-1].latitud == lat2
    assert puntos[-1].longitud == lon2
    distancia_total = distancia_geodesica(lat1, lon1, lat2, lon2)
    assert puntos[-1].distancia_acumulada_m == pytest.approx(distancia_total, rel=1e-6)


def test_muestrear_trayecto_numero_de_puntos():
    puntos = muestrear_trayecto(4.5981, -74.0761, 6.2518, -75.5636, 250)
    assert len(puntos) == 250


def test_muestrear_trayecto_distancia_monotona_creciente():
    puntos = muestrear_trayecto(4.5981, -74.0761, 6.2518, -75.5636, 200)
    distancias = [p.distancia_acumulada_m for p in puntos]
    assert distancias == sorted(distancias)
    # Estrictamente creciente entre puntos distintos (A != B en este caso).
    assert all(b > a for a, b in zip(distancias, distancias[1:]))


def test_muestrear_trayecto_puntos_coincidentes():
    """Si A y B son el mismo punto, no debe fallar: todas las distancias son 0."""
    puntos = muestrear_trayecto(4.6, -74.1, 4.6, -74.1, 200)
    assert len(puntos) == 200
    assert all(p.distancia_acumulada_m == pytest.approx(0.0, abs=1e-9) for p in puntos)
    assert all(p.latitud == 4.6 and p.longitud == -74.1 for p in puntos)


def test_muestrear_trayecto_requiere_al_menos_dos_puntos():
    with pytest.raises(ValueError):
        muestrear_trayecto(4.6, -74.1, 6.2, -75.5, 1)


def test_muestrear_trayecto_cruza_el_antimeridiano():
    """Un enlace que cruza la línea de cambio de fecha (lon 180/-180).

    Sin manejo correcto del antimeridiano, un algoritmo ingenuo podría
    calcular la distancia "larga" (dando la vuelta al mundo por el otro
    lado) en vez de la corta. La geodésica de pyproj resuelve esto
    correctamente; este test verifica que el resultado sea el trayecto
    corto y que las distancias acumuladas sigan siendo monótonas.
    """
    lat1, lon1 = 0.0, 179.5
    lat2, lon2 = 0.0, -179.5

    distancia_m = distancia_geodesica(lat1, lon1, lat2, lon2)
    # 1 grado de separación real (179.5 -> 180 -> -179.5), no ~359 grados.
    assert distancia_m == pytest.approx(111_319.49, rel=0.01)

    puntos = muestrear_trayecto(lat1, lon1, lat2, lon2, 200)
    distancias = [p.distancia_acumulada_m for p in puntos]
    assert all(b >= a for a, b in zip(distancias, distancias[1:]))
    assert puntos[-1].distancia_acumulada_m == pytest.approx(distancia_m, rel=1e-6)
