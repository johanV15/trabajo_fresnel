"""Tests de la capa HTTP (api/main.py): validación, traducción de errores,
y que ningún mensaje visible para el usuario quede en inglés o exponga
detalles internos (trazas, texto crudo de librerías).

No golpean la red real: `fuente_elevacion.consultar` se monkeypatchea por
test. El único caso "feliz" end-to-end con datos reales lo cubre
scripts/verificar_elevacion.py y las pruebas manuales de sustentación.
"""

import pytest
from fastapi.testclient import TestClient

import api.main as main_module
from core.elevation import ErrorLimiteTasa, ErrorRed, ErrorSinDatosDeElevacion


@pytest.fixture()
def client():
    # raise_server_exceptions=False: sin esto, TestClient relanza
    # cualquier excepción no manejada en el propio test (conveniente para
    # depurar, pero eso es justo lo que queremos poder probar aquí — que
    # un servidor ASGI real, vía uvicorn, SÍ la traduce a un 500 en
    # español gracias al exception_handler global).
    return TestClient(main_module.app, raise_server_exceptions=False)


def _cuerpo_valido(**overrides):
    cuerpo = {
        "punto_a": {"latitud": 4.5981, "longitud": -74.0761, "altura_torre_m": 30.0},
        "punto_b": {"latitud": 4.6879, "longitud": -74.0765, "altura_torre_m": 30.0},
        "frecuencia_valor": 5.0,
        "frecuencia_unidad": "GHz",
    }
    cuerpo.update(overrides)
    return cuerpo


def _sin_caracteres_no_ascii_sospechosos(texto: str) -> bool:
    """No es una prueba de idioma perfecta (imposible sin un detector de
    idioma de verdad), pero atrapa lo obvio: rutas de archivo del sistema
    y nombres de excepción de Python crudos no deberían aparecer nunca en
    un mensaje para el usuario."""
    marcadores_de_fuga = [
        "Traceback",
        "  File \"",
        "site-packages",
        ".py\", line",
        "Internal Server Error",
        "NoneType",
        "Errno",
    ]
    return not any(m in texto for m in marcadores_de_fuga)


# --- Validación de entrada ---


def test_analizar_puntos_iguales_400(client):
    r = client.post("/api/analizar", json=_cuerpo_valido(punto_b={
        "latitud": 4.5981, "longitud": -74.0761, "altura_torre_m": 30.0
    }))
    assert r.status_code == 400
    assert r.json()["detail"] == (
        "Los puntos A y B son el mismo lugar; no hay enlace que analizar."
    )


def test_analizar_frecuencia_cero_400(client):
    r = client.post("/api/analizar", json=_cuerpo_valido(frecuencia_valor=0))
    assert r.status_code == 400
    assert r.json()["detail"] == "la frecuencia debe ser mayor que cero"


def test_analizar_latitud_fuera_de_rango_400(client):
    cuerpo = _cuerpo_valido()
    cuerpo["punto_a"]["latitud"] = 95.0
    r = client.post("/api/analizar", json=cuerpo)
    assert r.status_code == 400
    assert "latitud" in r.json()["detail"]
    assert _sin_caracteres_no_ascii_sospechosos(r.json()["detail"])


def test_analizar_altura_torre_negativa_400(client):
    cuerpo = _cuerpo_valido()
    cuerpo["punto_a"]["altura_torre_m"] = -5.0
    r = client.post("/api/analizar", json=cuerpo)
    assert r.status_code == 400
    assert r.json()["detail"] == "la altura de la torre no puede ser negativa"


def test_analizar_k_invalido_400(client):
    r = client.post("/api/analizar", json=_cuerpo_valido(k=-1))
    assert r.status_code == 400
    assert "k" in r.json()["detail"]


# --- Traducción de errores de la capa de elevación (sin red real) ---


def test_error_limite_tasa_traducido_503(client, monkeypatch):
    def _lanzar(*a, **k):
        raise ErrorLimiteTasa("límite de tasa excedido")

    monkeypatch.setattr(main_module.fuente_elevacion, "consultar", _lanzar)
    r = client.post("/api/analizar", json=_cuerpo_valido())
    assert r.status_code == 503
    detalle = r.json()["detail"]
    assert "límite de tasa" in detalle
    assert _sin_caracteres_no_ascii_sospechosos(detalle)


def test_error_red_traducido_502(client, monkeypatch):
    def _lanzar(*a, **k):
        raise ErrorRed("no se pudo consultar Open-Topo-Data tras 3 intentos (fallo de red repetido)")

    monkeypatch.setattr(main_module.fuente_elevacion, "consultar", _lanzar)
    r = client.post("/api/analizar", json=_cuerpo_valido())
    assert r.status_code == 502
    detalle = r.json()["detail"]
    assert "no se pudo contactar" in detalle.lower()
    assert _sin_caracteres_no_ascii_sospechosos(detalle)


def test_error_sin_datos_de_elevacion_traducido_502(client, monkeypatch):
    def _lanzar(*a, **k):
        raise ErrorSinDatosDeElevacion(
            "el dataset SRTM no tiene elevación para uno o más puntos del trayecto"
        )

    monkeypatch.setattr(main_module.fuente_elevacion, "consultar", _lanzar)
    r = client.post("/api/analizar", json=_cuerpo_valido())
    assert r.status_code == 502
    detalle = r.json()["detail"]
    assert "mar" in detalle or "SRTM" in detalle
    assert _sin_caracteres_no_ascii_sospechosos(detalle)


# --- Red de seguridad: excepción totalmente inesperada ---


def test_error_inesperado_no_expone_traceback_ni_ingles(client, monkeypatch):
    """Simula un bug interno no previsto por ningún except específico
    (aquí, en analizar_enlace) y confirma que el usuario ve un mensaje en
    español, sin traceback ni "Internal Server Error"."""

    def _reventar(*a, **k):
        raise RuntimeError("boom interno inesperado")

    monkeypatch.setattr(main_module, "analizar_enlace", _reventar)
    r = client.post("/api/analizar", json=_cuerpo_valido())
    assert r.status_code == 500
    detalle = r.json()["detail"]
    assert _sin_caracteres_no_ascii_sospechosos(detalle)
    assert "boom interno inesperado" not in detalle
    assert "RuntimeError" not in detalle
    assert "Ocurrió un error inesperado" in detalle


# --- Demos ---


def test_demo_inexistente_404_en_espanol(client):
    r = client.get("/api/demos/no-existe")
    assert r.status_code == 404
    assert r.json()["detail"] == "No existe un caso demo con id 'no-existe'."


def test_listar_demos_200(client):
    r = client.get("/api/demos")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
