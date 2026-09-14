"""Cliente de elevaciones con caché persistente en disco.

Aísla el acceso a la API cartográfica del resto de `core/`: `geo.py` y
`fresnel.py` son puros y no dependen de red. Este módulo es la única
excepción, y solo porque no hay forma de conocer la elevación real del
terreno sin consultar una fuente externa (ver Contexto/PROYECTO-FRESNEL.md,
sección 5).

Requisito operativo: si la red falla durante la sustentación o la API
aplica rate limit, la aplicación debe seguir respondiendo con lo que ya
tenga cacheado. Por eso la caché en disco no es una optimización, es un
requisito de robustez.
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import httpx

_TAMANO_LOTE_PREDETERMINADO = 100
_INTERVALO_MINIMO_S_PREDETERMINADO = 1.0
_MAX_REINTENTOS_PREDETERMINADO = 3
_BACKOFF_BASE_S_PREDETERMINADO = 1.0
_TIMEOUT_S = 10.0

_PRECISION_DECIMALES_CACHE = 4
"""Redondeo de coordenadas para la clave de caché: 4 decimales de grado
equivalen a ~11 m en el ecuador (30 m / 111_320 m-por-grado ≈ 0.00027°, que
redondeado da un tamaño de celda de ~11 m). Se elige más fino que la celda
de 30 m del dataset SRTM, nunca más grueso, para no perder precisión
adicional a la que el propio dataset ya tiene."""


# --- Excepciones ---


class ErrorElevacion(Exception):
    """Base de los errores de la capa de elevaciones."""


class ErrorRed(ErrorElevacion):
    """Fallo de conectividad, timeout, o error transitorio del servidor,
    tras agotar los reintentos con backoff."""


class ErrorLimiteTasa(ErrorElevacion):
    """La API respondió con límite de tasa excedido (HTTP 429) de forma
    persistente, tras agotar los reintentos."""


class ErrorRespuestaInvalida(ErrorElevacion):
    """La API respondió (sin error de transporte) pero con un formato,
    estado o contenido que no se puede interpretar como elevaciones."""


class ErrorSinDatosDeElevacion(ErrorRespuestaInvalida):
    """La API respondió correctamente pero sin dato de elevación para uno o
    más puntos consultados — típicamente porque caen sobre el mar, donde
    el dataset SRTM no tiene cobertura terrestre. Es un caso normal y
    esperable en enlaces muy largos, no una respuesta malformada; se
    distingue de `ErrorRespuestaInvalida` para poder dar un mensaje
    específico y accionable en vez de uno genérico."""


# --- Interfaz ---


@dataclass(frozen=True)
class PuntoConsulta:
    """Una coordenada para la que se quiere conocer la elevación."""

    latitud: float
    longitud: float


class FuenteElevacion(ABC):
    """Cualquier proveedor de datos de elevación, real o de caché."""

    @abstractmethod
    def consultar(self, puntos: Sequence[PuntoConsulta]) -> List[float]:
        """Elevación en metros sobre el nivel del mar de cada punto, en
        el mismo orden que `puntos`."""
        raise NotImplementedError


# --- Implementación real: Open-Topo-Data ---


class OpenTopoData(FuenteElevacion):
    """Cliente del servicio público Open-Topo-Data (dataset SRTM 30 m).

    Sin API key. El servicio documenta límites de ~100 ubicaciones por
    llamada y ~1 llamada por segundo; ambos se respetan aquí de forma
    explícita en vez de confiar en que el servidor los tolere en silencio.

    `tamano_lote`, `intervalo_minimo_s`, `max_reintentos` y
    `backoff_base_s` son configurables sobre todo para poder probar la
    lógica de lotes, rate limit y reintentos sin esperas reales en los
    tests; en producción se usan los valores por defecto documentados
    arriba.
    """

    def __init__(
        self,
        dataset: str = "srtm30m",
        url_base: str = "https://api.opentopodata.org/v1",
        cliente_http: Optional[httpx.Client] = None,
        tamano_lote: int = _TAMANO_LOTE_PREDETERMINADO,
        intervalo_minimo_s: float = _INTERVALO_MINIMO_S_PREDETERMINADO,
        max_reintentos: int = _MAX_REINTENTOS_PREDETERMINADO,
        backoff_base_s: float = _BACKOFF_BASE_S_PREDETERMINADO,
    ) -> None:
        self._dataset = dataset
        self._url_base = url_base
        self._cliente = cliente_http or httpx.Client(timeout=_TIMEOUT_S)
        self._tamano_lote = tamano_lote
        self._intervalo_minimo_s = intervalo_minimo_s
        self._max_reintentos = max_reintentos
        self._backoff_base_s = backoff_base_s
        self._ultimo_llamado_ts: Optional[float] = None

    def consultar(self, puntos: Sequence[PuntoConsulta]) -> List[float]:
        elevaciones: List[float] = []
        for inicio in range(0, len(puntos), self._tamano_lote):
            lote = puntos[inicio : inicio + self._tamano_lote]
            elevaciones.extend(self._consultar_lote(lote))
        return elevaciones

    def _consultar_lote(self, lote: Sequence[PuntoConsulta]) -> List[float]:
        ubicaciones = "|".join(f"{p.latitud},{p.longitud}" for p in lote)
        url = f"{self._url_base}/{self._dataset}"

        ultimo_error: Optional[Exception] = None
        for intento in range(self._max_reintentos):
            self._respetar_rate_limit()
            try:
                respuesta = self._cliente.get(url, params={"locations": ubicaciones})
            except httpx.RequestError as exc:
                ultimo_error = exc
                self._esperar_backoff(intento)
                continue

            if respuesta.status_code == 429:
                ultimo_error = ErrorLimiteTasa(
                    "Open-Topo-Data respondió 429 (límite de tasa excedido)"
                )
                self._esperar_backoff(intento)
                continue

            if respuesta.status_code >= 500:
                ultimo_error = ErrorRed(
                    f"Open-Topo-Data respondió {respuesta.status_code} "
                    "(error transitorio del servidor)"
                )
                self._esperar_backoff(intento)
                continue

            if respuesta.status_code != 200:
                # Error del lado del cliente (400, etc.): no es transitorio,
                # reintentar no lo va a arreglar. No se incluye el cuerpo
                # crudo de la respuesta en el mensaje: puede venir en
                # inglés o en HTML, y este mensaje es visible para el
                # usuario final (vía api/main.py).
                raise ErrorRespuestaInvalida(
                    f"Open-Topo-Data respondió con un error inesperado "
                    f"(código {respuesta.status_code})"
                )

            return self._parsear_respuesta(respuesta, len(lote))

        # No se interpola `ultimo_error` en el mensaje: si viene de
        # httpx (ej. httpx.ConnectError), su texto está en inglés, y este
        # mensaje es lo que ve el usuario final tal cual (vía
        # api/main.py). El detalle técnico queda disponible para quien
        # depure en consola/logs a través de `raise ... from ultimo_error`.
        if isinstance(ultimo_error, ErrorLimiteTasa):
            raise ultimo_error
        raise ErrorRed(
            f"no se pudo consultar Open-Topo-Data tras {self._max_reintentos} "
            "intentos (fallo de red repetido)"
        ) from ultimo_error

    @staticmethod
    def _parsear_respuesta(respuesta: httpx.Response, n_esperado: int) -> List[float]:
        try:
            cuerpo = respuesta.json()
        except ValueError as exc:
            raise ErrorRespuestaInvalida("la respuesta no es JSON válido") from exc

        if cuerpo.get("status") != "OK":
            raise ErrorRespuestaInvalida(
                f"estado inesperado en la respuesta: {cuerpo.get('status')!r}"
            )

        resultados = cuerpo.get("results")
        if not isinstance(resultados, list) or len(resultados) != n_esperado:
            n_llegados = len(resultados) if isinstance(resultados, list) else "ninguno"
            raise ErrorRespuestaInvalida(
                f"se esperaban {n_esperado} resultados, llegaron {n_llegados}"
            )

        if any(r.get("elevation") is None for r in resultados if isinstance(r, dict)):
            raise ErrorSinDatosDeElevacion(
                "el dataset SRTM no tiene elevación para uno o más puntos del "
                "trayecto (puede ser que caigan sobre el mar, o fuera de la "
                "cobertura de SRTM, que llega hasta ~60° de latitud norte y sur)"
            )

        try:
            return [float(r["elevation"]) for r in resultados]
        except (KeyError, TypeError, ValueError) as exc:
            raise ErrorRespuestaInvalida(
                "un resultado no trae 'elevation' numérica"
            ) from exc

    def _respetar_rate_limit(self) -> None:
        """Duerme lo necesario para que no pase menos de `intervalo_minimo_s`
        entre el inicio de una llamada HTTP y el de la siguiente."""
        if self._ultimo_llamado_ts is not None:
            transcurrido = time.monotonic() - self._ultimo_llamado_ts
            faltante = self._intervalo_minimo_s - transcurrido
            if faltante > 0:
                time.sleep(faltante)
        self._ultimo_llamado_ts = time.monotonic()

    def _esperar_backoff(self, intento: int) -> None:
        """Backoff exponencial: intento 0 espera `backoff_base_s`, el 1 el
        doble, el 2 el cuádruple, etc."""
        time.sleep(self._backoff_base_s * (2**intento))


# --- Envoltorio de caché en disco ---


def clave_cache(lat: float, lon: float) -> str:
    """Clave de caché para una coordenada, con el mismo redondeo que usa
    internamente `CacheElevacionEnDisco`. Pública para que otros scripts
    (por ejemplo `scripts/generar_demos.py`) puedan congelar elevaciones en
    un formato compatible con el que esta clase espera leer."""
    lat_r = round(lat, _PRECISION_DECIMALES_CACHE)
    lon_r = round(lon, _PRECISION_DECIMALES_CACHE)
    return f"{lat_r:.4f},{lon_r:.4f}"


class CacheElevacionEnDisco(FuenteElevacion):
    """Envuelve otra `FuenteElevacion` con una caché persistente en JSON.

    Se guarda como un diccionario `"lat,lon" -> elevación` en un archivo de
    texto plano legible (no un pickle) para poder inspeccionarlo o editarlo
    a mano si hiciera falta durante la sustentación, y para que no dependa
    de la versión de Python que lo escribió.

    Solo consulta la fuente envuelta para los puntos que faltan en caché;
    los que ya están cacheados no generan ninguna llamada de red.
    """

    def __init__(self, fuente: FuenteElevacion, ruta_cache: Path) -> None:
        self._fuente = fuente
        self._ruta_cache = ruta_cache
        self._datos: Dict[str, float] = self._cargar()

    def _cargar(self) -> Dict[str, float]:
        if not self._ruta_cache.exists():
            return {}
        try:
            return json.loads(self._ruta_cache.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _guardar(self) -> None:
        self._ruta_cache.parent.mkdir(parents=True, exist_ok=True)
        self._ruta_cache.write_text(
            json.dumps(self._datos, indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )

    def consultar(self, puntos: Sequence[PuntoConsulta]) -> List[float]:
        claves = [clave_cache(p.latitud, p.longitud) for p in puntos]
        indices_faltantes = [i for i, c in enumerate(claves) if c not in self._datos]

        if indices_faltantes:
            puntos_faltantes = [puntos[i] for i in indices_faltantes]
            elevaciones_nuevas = self._fuente.consultar(puntos_faltantes)
            for i, elevacion in zip(indices_faltantes, elevaciones_nuevas):
                self._datos[claves[i]] = elevacion
            self._guardar()

        return [self._datos[c] for c in claves]

    def precargar(self, elevaciones: Dict[str, float]) -> None:
        """Agrega entradas ya conocidas a la caché en memoria sin consultar
        la fuente envuelta ni tocar el archivo en disco de inmediato.

        Existe para los casos demo (sección 7.1 de la especificación): sus
        elevaciones vienen congeladas en `data/demos/` versionadas en el
        repositorio, y se cargan aquí al iniciar la aplicación para que
        respondan sin red aunque la API de elevación no esté disponible.
        """
        self._datos.update(elevaciones)
