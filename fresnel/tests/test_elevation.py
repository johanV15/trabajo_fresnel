"""Tests de core/elevation.py: red simulada (httpx.MockTransport), sin llamadas reales."""

import json

import httpx
import pytest

from core.elevation import (
    CacheElevacionEnDisco,
    ErrorLimiteTasa,
    ErrorRed,
    ErrorRespuestaInvalida,
    ErrorSinDatosDeElevacion,
    OpenTopoData,
    PuntoConsulta,
)


def _cliente_con_handler(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _respuesta_ok(n: int, elevacion_base: float = 100.0) -> httpx.Response:
    resultados = [{"elevation": elevacion_base + i, "location": {}} for i in range(n)]
    return httpx.Response(200, json={"status": "OK", "results": resultados})


# --- Caso feliz y batching ---


def test_consultar_un_solo_lote():
    llamadas = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(request)
        return _respuesta_ok(2, elevacion_base=2600.0)

    fuente = OpenTopoData(
        cliente_http=_cliente_con_handler(handler),
        intervalo_minimo_s=0.0,
    )
    puntos = [PuntoConsulta(4.6, -74.1), PuntoConsulta(4.7, -74.2)]
    elevaciones = fuente.consultar(puntos)

    assert elevaciones == [2600.0, 2601.0]
    assert len(llamadas) == 1


def test_consultar_agrupa_en_lotes():
    llamadas = []

    def handler(request: httpx.Request) -> httpx.Response:
        n = len(request.url.params["locations"].split("|"))
        llamadas.append(n)
        return _respuesta_ok(n)

    fuente = OpenTopoData(
        cliente_http=_cliente_con_handler(handler),
        tamano_lote=2,
        intervalo_minimo_s=0.0,
    )
    puntos = [PuntoConsulta(0.0, float(i)) for i in range(5)]
    elevaciones = fuente.consultar(puntos)

    assert len(elevaciones) == 5
    # 5 puntos con lotes de 2 -> lotes de tamaño 2, 2, 1
    assert llamadas == [2, 2, 1]


# --- Rate limit ---


def test_respeta_intervalo_minimo_entre_llamadas(monkeypatch):
    tiempos = iter([100.0, 100.0, 100.3, 100.3])  # ts inicial, tras dormir, etc.
    reloj = {"t": 100.0}

    def monotonic_falso():
        return reloj["t"]

    dormidas = []

    def sleep_falso(segundos):
        dormidas.append(segundos)
        reloj["t"] += segundos

    monkeypatch.setattr("core.elevation.time.monotonic", monotonic_falso)
    monkeypatch.setattr("core.elevation.time.sleep", sleep_falso)

    def handler(request: httpx.Request) -> httpx.Response:
        reloj["t"] += 0.1  # simula que la llamada misma toma tiempo
        return _respuesta_ok(1)

    fuente = OpenTopoData(
        cliente_http=_cliente_con_handler(handler),
        tamano_lote=1,
        intervalo_minimo_s=1.0,
    )
    puntos = [PuntoConsulta(0.0, 0.0), PuntoConsulta(0.0, 1.0)]
    fuente.consultar(puntos)

    # La segunda llamada debe haber esperado ~0.9 s (1.0 - 0.1 transcurrido).
    assert any(d == pytest.approx(0.9, abs=1e-9) for d in dormidas)


# --- Reintentos con backoff ---


def test_reintenta_tras_error_5xx_y_luego_tiene_exito(monkeypatch):
    monkeypatch.setattr("core.elevation.time.sleep", lambda s: None)
    respuestas = iter([httpx.Response(503), _respuesta_ok(1)])

    def handler(request: httpx.Request) -> httpx.Response:
        return next(respuestas)

    fuente = OpenTopoData(
        cliente_http=_cliente_con_handler(handler),
        intervalo_minimo_s=0.0,
        max_reintentos=3,
        backoff_base_s=0.0,
    )
    elevaciones = fuente.consultar([PuntoConsulta(4.6, -74.1)])
    assert elevaciones == [100.0]


def test_error_red_tras_agotar_reintentos(monkeypatch):
    monkeypatch.setattr("core.elevation.time.sleep", lambda s: None)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin conexión", request=request)

    fuente = OpenTopoData(
        cliente_http=_cliente_con_handler(handler),
        intervalo_minimo_s=0.0,
        max_reintentos=2,
        backoff_base_s=0.0,
    )
    with pytest.raises(ErrorRed):
        fuente.consultar([PuntoConsulta(4.6, -74.1)])


def test_error_limite_tasa_tras_agotar_reintentos(monkeypatch):
    monkeypatch.setattr("core.elevation.time.sleep", lambda s: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    fuente = OpenTopoData(
        cliente_http=_cliente_con_handler(handler),
        intervalo_minimo_s=0.0,
        max_reintentos=2,
        backoff_base_s=0.0,
    )
    with pytest.raises(ErrorLimiteTasa):
        fuente.consultar([PuntoConsulta(4.6, -74.1)])


def test_error_respuesta_invalida_status_no_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "INVALID_REQUEST"})

    fuente = OpenTopoData(cliente_http=_cliente_con_handler(handler), intervalo_minimo_s=0.0)
    with pytest.raises(ErrorRespuestaInvalida):
        fuente.consultar([PuntoConsulta(4.6, -74.1)])


def test_error_respuesta_invalida_json_roto():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"esto no es json")

    fuente = OpenTopoData(cliente_http=_cliente_con_handler(handler), intervalo_minimo_s=0.0)
    with pytest.raises(ErrorRespuestaInvalida):
        fuente.consultar([PuntoConsulta(4.6, -74.1)])


def test_error_respuesta_invalida_numero_de_resultados_incorrecto():
    def handler(request: httpx.Request) -> httpx.Response:
        # Se piden 2 puntos pero el servidor solo devuelve 1 resultado.
        return httpx.Response(200, json={"status": "OK", "results": [{"elevation": 100.0}]})

    fuente = OpenTopoData(cliente_http=_cliente_con_handler(handler), intervalo_minimo_s=0.0)
    with pytest.raises(ErrorRespuestaInvalida):
        fuente.consultar([PuntoConsulta(4.6, -74.1), PuntoConsulta(4.7, -74.2)])


def test_error_sin_datos_de_elevacion_punto_sobre_el_mar():
    """Open-Topo-Data responde OK pero con elevation:null para un punto sin
    cobertura SRTM (típicamente mar). Debe distinguirse de una respuesta
    malformada para poder dar un mensaje accionable ("elige tierra firme")."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "OK",
                "results": [{"elevation": 100.0}, {"elevation": None}],
            },
        )

    fuente = OpenTopoData(cliente_http=_cliente_con_handler(handler), intervalo_minimo_s=0.0)
    with pytest.raises(ErrorSinDatosDeElevacion):
        fuente.consultar([PuntoConsulta(4.6, -74.1), PuntoConsulta(0.0, -80.0)])


def test_error_respuesta_invalida_sin_campo_elevation():
    """Falta el campo 'elevation' por completo (ni siquiera null):
    `.get('elevation')` devuelve None igual que en el caso de "sin datos",
    así que también se clasifica como ErrorSinDatosDeElevacion (subclase
    de ErrorRespuestaInvalida) — sigue sin ser un crash silencioso."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "OK", "results": [{"location": {}}]})

    fuente = OpenTopoData(cliente_http=_cliente_con_handler(handler), intervalo_minimo_s=0.0)
    with pytest.raises(ErrorRespuestaInvalida):
        fuente.consultar([PuntoConsulta(4.6, -74.1)])


def test_error_respuesta_invalida_elevation_no_numerica():
    """Caso distinto del anterior: el campo 'elevation' SÍ está presente y
    no es null, pero no es un número (respuesta realmente malformada, no
    "sin datos en esta ubicación")."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "OK", "results": [{"elevation": "no-numerico"}]})

    fuente = OpenTopoData(cliente_http=_cliente_con_handler(handler), intervalo_minimo_s=0.0)
    with pytest.raises(ErrorRespuestaInvalida) as excinfo:
        fuente.consultar([PuntoConsulta(4.6, -74.1)])
    assert not isinstance(excinfo.value, ErrorSinDatosDeElevacion)


def test_error_respuesta_invalida_400_no_reintenta():
    llamadas = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(request)
        return httpx.Response(400, text="bad request")

    fuente = OpenTopoData(cliente_http=_cliente_con_handler(handler), intervalo_minimo_s=0.0)
    with pytest.raises(ErrorRespuestaInvalida):
        fuente.consultar([PuntoConsulta(4.6, -74.1)])
    assert len(llamadas) == 1  # error no transitorio: sin reintentos


# --- Caché en disco ---


class _FuenteFalsa:
    def __init__(self):
        self.llamadas = []

    def consultar(self, puntos):
        self.llamadas.append(list(puntos))
        return [1000.0 + i for i in range(len(puntos))]


def test_cache_escribe_json_legible(tmp_path):
    ruta = tmp_path / "elevaciones.json"
    fuente_falsa = _FuenteFalsa()
    cache = CacheElevacionEnDisco(fuente_falsa, ruta)

    resultado = cache.consultar([PuntoConsulta(4.6, -74.1)])
    assert resultado == [1000.0]

    contenido = json.loads(ruta.read_text(encoding="utf-8"))
    assert contenido == {"4.6000,-74.1000": 1000.0}


def test_cache_segunda_consulta_no_llama_a_la_fuente(tmp_path):
    ruta = tmp_path / "elevaciones.json"
    fuente_falsa = _FuenteFalsa()
    cache = CacheElevacionEnDisco(fuente_falsa, ruta)

    punto = PuntoConsulta(4.6, -74.1)
    cache.consultar([punto])
    assert len(fuente_falsa.llamadas) == 1

    cache.consultar([punto])
    assert len(fuente_falsa.llamadas) == 1  # no aumentó: vino de caché


def test_cache_persiste_entre_instancias(tmp_path):
    ruta = tmp_path / "elevaciones.json"
    fuente_falsa_1 = _FuenteFalsa()
    CacheElevacionEnDisco(fuente_falsa_1, ruta).consultar([PuntoConsulta(4.6, -74.1)])

    fuente_falsa_2 = _FuenteFalsa()
    resultado = CacheElevacionEnDisco(fuente_falsa_2, ruta).consultar([PuntoConsulta(4.6, -74.1)])

    assert resultado == [1000.0]
    assert len(fuente_falsa_2.llamadas) == 0  # el archivo ya lo tenía cacheado


def test_cache_redondeo_coincide_puntos_muy_cercanos(tmp_path):
    ruta = tmp_path / "elevaciones.json"
    fuente_falsa = _FuenteFalsa()
    cache = CacheElevacionEnDisco(fuente_falsa, ruta)

    cache.consultar([PuntoConsulta(4.60001, -74.10001)])
    assert len(fuente_falsa.llamadas) == 1

    # Redondea a la misma clave de 4 decimales -> no debe volver a consultar.
    resultado = cache.consultar([PuntoConsulta(4.60002, -74.10002)])
    assert resultado == [1000.0]
    assert len(fuente_falsa.llamadas) == 1


def test_cache_solo_consulta_los_puntos_faltantes(tmp_path):
    ruta = tmp_path / "elevaciones.json"
    fuente_falsa = _FuenteFalsa()
    cache = CacheElevacionEnDisco(fuente_falsa, ruta)

    cache.consultar([PuntoConsulta(4.6, -74.1)])
    resultado = cache.consultar([PuntoConsulta(4.6, -74.1), PuntoConsulta(5.0, -75.0)])

    assert resultado == [1000.0, 1000.0]  # el segundo punto es la única consulta nueva
    assert len(fuente_falsa.llamadas) == 2
    assert len(fuente_falsa.llamadas[1]) == 1  # solo el punto nuevo, no el ya cacheado


def test_cache_archivo_corrupto_se_ignora_sin_fallar(tmp_path):
    ruta = tmp_path / "elevaciones.json"
    ruta.write_text("esto no es json valido", encoding="utf-8")

    fuente_falsa = _FuenteFalsa()
    cache = CacheElevacionEnDisco(fuente_falsa, ruta)
    resultado = cache.consultar([PuntoConsulta(4.6, -74.1)])
    assert resultado == [1000.0]


def test_precargar_evita_consultar_la_fuente(tmp_path):
    """precargar() es lo que usa api/main.py al arrancar para que los casos
    demo respondan sin red (ver Contexto/PROYECTO-FRESNEL.md, sección 7.3)."""
    ruta = tmp_path / "elevaciones.json"
    fuente_falsa = _FuenteFalsa()
    cache = CacheElevacionEnDisco(fuente_falsa, ruta)

    cache.precargar({"4.6000,-74.1000": 2613.0})
    resultado = cache.consultar([PuntoConsulta(4.6, -74.1)])

    assert resultado == [2613.0]
    assert len(fuente_falsa.llamadas) == 0
