"""Tests de core/fresnel.py: radio F1, curvatura, análisis y veredicto."""

import pytest

from core.fresnel import (
    EstadoEnlace,
    MuestraTerreno,
    abultamiento_curvatura,
    analizar_enlace,
    radio_fresnel,
)


# --- Caso de validación obligatorio (sección 4.5 de la especificación) ---


def test_validacion_radio_f1_10km_5ghz():
    """Enlace de 10 km a 5 GHz: radio máximo de F1 en el centro ≈ 12,25 m."""
    radio_centro = radio_fresnel(5_000.0, 5_000.0, 5e9)
    assert radio_centro == pytest.approx(12.25, abs=0.05)


def test_validacion_abultamiento_10km_k_estandar():
    """Mismo enlace, abultamiento por curvatura en el centro con k=4/3 ≈ 1,47 m."""
    abultamiento_centro = abultamiento_curvatura(5_000.0, 5_000.0, 4.0 / 3.0)
    assert abultamiento_centro == pytest.approx(1.47, abs=0.01)


def test_validacion_analizar_enlace_extremo_a_extremo():
    """El mismo caso de validación, pasando por analizar_enlace completo."""
    perfil = [
        MuestraTerreno(latitud=0.0, longitud=0.0, distancia_acumulada_m=0.0, elevacion_msnm=0.0),
        MuestraTerreno(latitud=0.0, longitud=0.0, distancia_acumulada_m=5_000.0, elevacion_msnm=0.0),
        MuestraTerreno(latitud=0.0, longitud=0.0, distancia_acumulada_m=10_000.0, elevacion_msnm=0.0),
    ]
    resultado = analizar_enlace(
        perfil,
        elevacion_msnm_a=0.0,
        altura_torre_a_m=30.0,
        elevacion_msnm_b=0.0,
        altura_torre_b_m=30.0,
        frecuencia_hz=5e9,
    )
    assert resultado.radio_f1_maximo_m == pytest.approx(12.25, abs=0.05)
    assert resultado.distancia_total_m == pytest.approx(10_000.0)


# --- radio_fresnel: casos básicos ---


def test_radio_fresnel_cero_en_las_antenas():
    assert radio_fresnel(0.0, 10_000.0, 5e9) == pytest.approx(0.0, abs=1e-9)
    assert radio_fresnel(10_000.0, 0.0, 5e9) == pytest.approx(0.0, abs=1e-9)


def test_radio_fresnel_distancia_total_cero():
    assert radio_fresnel(0.0, 0.0, 5e9) == 0.0


def test_radio_fresnel_aumenta_con_menor_frecuencia():
    """A menor frecuencia, mayor longitud de onda, mayor zona de Fresnel."""
    radio_5ghz = radio_fresnel(5_000.0, 5_000.0, 5e9)
    radio_900mhz = radio_fresnel(5_000.0, 5_000.0, 900e6)
    assert radio_900mhz > radio_5ghz


# --- abultamiento_curvatura ---


def test_abultamiento_cero_en_las_antenas():
    assert abultamiento_curvatura(0.0, 10_000.0, 4.0 / 3.0) == pytest.approx(0.0, abs=1e-9)


def test_abultamiento_disminuye_con_k_mayor():
    """Un k mayor modela más refracción atmosférica: Tierra 'efectiva' más grande, menos curvatura aparente."""
    abultamiento_k_estandar = abultamiento_curvatura(5_000.0, 5_000.0, 4.0 / 3.0)
    abultamiento_k_mayor = abultamiento_curvatura(5_000.0, 5_000.0, 2.0)
    assert abultamiento_k_mayor < abultamiento_k_estandar


# --- analizar_enlace: casos sintéticos ---


def _perfil_plano(distancia_total_m: float, elevacion_msnm: float, n: int = 5) -> list:
    paso = distancia_total_m / (n - 1)
    return [
        MuestraTerreno(
            latitud=0.0,
            longitud=0.0,
            distancia_acumulada_m=i * paso,
            elevacion_msnm=elevacion_msnm,
        )
        for i in range(n)
    ]


def test_terreno_plano_sin_obstaculos_es_viable():
    """Terreno plano y antenas con torre razonable: debe dar VIABLE con amplio margen."""
    perfil = _perfil_plano(10_000.0, elevacion_msnm=100.0)
    resultado = analizar_enlace(
        perfil,
        elevacion_msnm_a=100.0,
        altura_torre_a_m=30.0,
        elevacion_msnm_b=100.0,
        altura_torre_b_m=30.0,
        frecuencia_hz=5e9,
    )
    assert resultado.veredicto.estado is EstadoEnlace.VIABLE
    assert resultado.veredicto.despeje_pct_critico > 60.0
    assert "VIABLE" in resultado.veredicto.mensaje


def test_pico_invade_parcialmente_la_zona_fresnel():
    """Un pico que reduce la holgura por debajo del 60% pero sin bloquear la LOS."""
    perfil = [
        MuestraTerreno(latitud=4.0, longitud=-74.0, distancia_acumulada_m=0.0, elevacion_msnm=100.0),
        MuestraTerreno(latitud=4.05, longitud=-74.05, distancia_acumulada_m=5_000.0, elevacion_msnm=105.0),
        MuestraTerreno(latitud=4.1, longitud=-74.1, distancia_acumulada_m=10_000.0, elevacion_msnm=100.0),
    ]
    resultado = analizar_enlace(
        perfil,
        elevacion_msnm_a=100.0,
        altura_torre_a_m=10.0,
        elevacion_msnm_b=100.0,
        altura_torre_b_m=10.0,
        frecuencia_hz=5e9,
    )
    assert resultado.veredicto.estado is EstadoEnlace.INVASION_FRESNEL
    assert 0.0 <= resultado.veredicto.despeje_pct_critico < 60.0
    assert resultado.veredicto.distancia_punto_critico_m == pytest.approx(5_000.0)
    assert resultado.veredicto.latitud_critica == pytest.approx(4.05)
    assert "OBSTRUCCIÓN" in resultado.veredicto.mensaje


def test_pico_bloquea_la_linea_de_vista():
    """Un pico más alto que la propia línea de vista directa: LOS_BLOQUEADA."""
    perfil = [
        MuestraTerreno(latitud=4.0, longitud=-74.0, distancia_acumulada_m=0.0, elevacion_msnm=100.0),
        MuestraTerreno(latitud=4.05, longitud=-74.05, distancia_acumulada_m=5_000.0, elevacion_msnm=115.0),
        MuestraTerreno(latitud=4.1, longitud=-74.1, distancia_acumulada_m=10_000.0, elevacion_msnm=100.0),
    ]
    resultado = analizar_enlace(
        perfil,
        elevacion_msnm_a=100.0,
        altura_torre_a_m=10.0,
        elevacion_msnm_b=100.0,
        altura_torre_b_m=10.0,
        frecuencia_hz=5e9,
    )
    assert resultado.veredicto.estado is EstadoEnlace.LOS_BLOQUEADA
    assert resultado.veredicto.despeje_pct_critico < 0.0
    assert "BLOQUEADA" in resultado.veredicto.mensaje


def test_analizar_enlace_requiere_al_menos_dos_muestras():
    perfil = [
        MuestraTerreno(latitud=0.0, longitud=0.0, distancia_acumulada_m=0.0, elevacion_msnm=0.0)
    ]
    with pytest.raises(ValueError):
        analizar_enlace(
            perfil,
            elevacion_msnm_a=0.0,
            altura_torre_a_m=10.0,
            elevacion_msnm_b=0.0,
            altura_torre_b_m=10.0,
            frecuencia_hz=5e9,
        )


def test_altura_torre_cero_es_valida():
    """Torre de altura cero: la altura efectiva de esa antena es la elevación del terreno."""
    perfil = _perfil_plano(10_000.0, elevacion_msnm=100.0)
    resultado = analizar_enlace(
        perfil,
        elevacion_msnm_a=100.0,
        altura_torre_a_m=0.0,
        elevacion_msnm_b=100.0,
        altura_torre_b_m=0.0,
        frecuencia_hz=5e9,
    )
    assert resultado.altura_efectiva_a_m == pytest.approx(100.0)
    assert resultado.altura_efectiva_b_m == pytest.approx(100.0)
    # Con torres a ras de suelo y terreno plano, la LOS coincide con el
    # terreno salvo por el abultamiento de curvatura: holgura negativa.
    assert resultado.veredicto.estado is EstadoEnlace.LOS_BLOQUEADA


def test_perfil_solo_con_extremos_no_tiene_punto_critico():
    """Un perfil de exactamente 2 muestras (A y B) no tiene ningún punto
    intermedio: el radio F1 es cero en ambas (están en las antenas), así
    que no hay ninguna muestra con despeje definido y no se puede emitir
    veredicto. Caso degenerado, no se espera en uso real (el muestreo
    real siempre da >=200 puntos), pero debe fallar explícitamente."""
    perfil = [
        MuestraTerreno(latitud=0.0, longitud=0.0, distancia_acumulada_m=0.0, elevacion_msnm=100.0),
        MuestraTerreno(latitud=0.0, longitud=0.0, distancia_acumulada_m=10_000.0, elevacion_msnm=100.0),
    ]
    with pytest.raises(ValueError):
        analizar_enlace(
            perfil,
            elevacion_msnm_a=100.0,
            altura_torre_a_m=10.0,
            elevacion_msnm_b=100.0,
            altura_torre_b_m=10.0,
            frecuencia_hz=5e9,
        )
