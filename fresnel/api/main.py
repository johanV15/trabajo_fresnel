"""API HTTP del diagnóstico de primera zona de Fresnel.

Esta capa orquesta `core/` (que no sabe nada de HTTP ni de red) y traduce
sus resultados y errores a peticiones/respuestas HTTP. No reimplementa
ninguna fórmula: solo valida entrada, llama a `core/` en orden, y da forma
a la salida.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from enum import Enum
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from core.elevation import (
    CacheElevacionEnDisco,
    ErrorLimiteTasa,
    ErrorRed,
    ErrorRespuestaInvalida,
    ErrorSinDatosDeElevacion,
    OpenTopoData,
    PuntoConsulta,
)
from core.fresnel import (
    K_ESTANDAR,
    CRITERIO_DESPEJE_ESTANDAR_PCT,
    MuestraTerreno,
    analizar_enlace,
)
from core.geo import distancia_geodesica, muestrear_trayecto, numero_muestras_recomendado

_DIRECTORIO_FRESNEL = Path(__file__).resolve().parent.parent
_RUTA_CACHE_ELEVACION = _DIRECTORIO_FRESNEL / "cache" / "elevaciones.json"
_DIRECTORIO_DEMOS = _DIRECTORIO_FRESNEL / "data" / "demos"
_DIRECTORIO_WEB = _DIRECTORIO_FRESNEL / "web"

_DISTANCIA_ADVERTENCIA_M = 200_000.0
"""Más allá de esta distancia el modelo de radioenlace terrestre (línea de
vista + zona de Fresnel) deja de ser un supuesto realista, aunque el
cálculo geodésico y las fórmulas sigan siendo matemáticamente válidos."""

fuente_elevacion = CacheElevacionEnDisco(OpenTopoData(), _RUTA_CACHE_ELEVACION)


# --- Esquemas de entrada ---


class UnidadFrecuencia(str, Enum):
    """Unidad en la que viene expresado el valor numérico de la frecuencia."""

    HZ = "Hz"
    MHZ = "MHz"
    GHZ = "GHz"


_FACTOR_A_HZ = {
    UnidadFrecuencia.HZ: 1.0,
    UnidadFrecuencia.MHZ: 1e6,
    UnidadFrecuencia.GHZ: 1e9,
}


def _frecuencia_a_hz(valor: float, unidad: UnidadFrecuencia) -> float:
    return valor * _FACTOR_A_HZ[unidad]


class PuntoEntrada(BaseModel):
    """Una antena: su ubicación y la altura de su torre sobre el terreno."""

    latitud: float = Field(
        ...,
        ge=-90,
        le=90,
        description="Latitud en grados decimales (WGS-84).",
        examples=[4.5981],
    )
    longitud: float = Field(
        ...,
        ge=-180,
        le=180,
        description="Longitud en grados decimales (WGS-84).",
        examples=[-74.0761],
    )
    altura_torre_m: float = Field(
        ...,
        ge=0,
        description="Altura de la torre sobre el nivel del terreno, en metros "
        "(no sobre el nivel del mar; la elevación MSNM se resuelve automáticamente).",
        examples=[30.0],
    )


class SolicitudAnalisis(BaseModel):
    """Parámetros de entrada para diagnosticar un enlace de radio."""

    punto_a: PuntoEntrada
    punto_b: PuntoEntrada
    frecuencia_valor: float = Field(
        ...,
        gt=0,
        description="Valor numérico de la frecuencia de operación del enlace, "
        "en la unidad indicada por `frecuencia_unidad`.",
        examples=[5.0],
    )
    frecuencia_unidad: UnidadFrecuencia = Field(
        default=UnidadFrecuencia.GHZ,
        description="Unidad del valor de frecuencia.",
    )
    k: float = Field(
        default=K_ESTANDAR,
        gt=0,
        le=3.0,
        description="Factor de radio terrestre efectivo, para modelar la "
        "refracción atmosférica. 4/3 (≈1.333) es el valor estándar para "
        "atmósfera normal; en climas tropicales húmedos puede desviarse.",
        examples=[1.333],
    )
    criterio_despeje_pct: float = Field(
        default=CRITERIO_DESPEJE_ESTANDAR_PCT,
        ge=0,
        le=100,
        description="Porcentaje mínimo del radio de la primera zona de "
        "Fresnel que debe quedar libre de obstáculos (recomendación ITU-R "
        "P.530: 60%).",
        examples=[60.0],
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "punto_a": {
                        "latitud": 4.5981,
                        "longitud": -74.0761,
                        "altura_torre_m": 30.0,
                    },
                    "punto_b": {
                        "latitud": 4.6879,
                        "longitud": -74.0765,
                        "altura_torre_m": 30.0,
                    },
                    "frecuencia_valor": 5.0,
                    "frecuencia_unidad": "GHz",
                    "k": 1.333,
                    "criterio_despeje_pct": 60.0,
                }
            ]
        }
    }

    @model_validator(mode="after")
    def _puntos_deben_ser_distintos(self) -> "SolicitudAnalisis":
        # No se valida acá con un raise: se deja pasar y es la capa de
        # endpoint la que decide el código de estado (400) y el mensaje
        # exacto en español, para no depender del formato de error de
        # Pydantic en el caso más importante de la sección 5 de decisiones.
        return self


# --- Esquemas de salida ---


class MuestraAnalisisSalida(BaseModel):
    """Resultado del análisis de Fresnel en un punto del trayecto."""

    latitud: float
    longitud: float
    distancia_acumulada_m: float = Field(description="Distancia desde el punto A, en metros.")
    radio_f1_m: float = Field(description="Radio de la primera zona de Fresnel en este punto.")
    abultamiento_m: float = Field(description="Abultamiento aparente por curvatura terrestre.")
    altura_linea_vista_m: float = Field(description="Altura de la línea de vista directa A-B, MSNM.")
    holgura_m: float = Field(description="Línea de vista menos (terreno + abultamiento), en metros.")
    despeje_pct: Optional[float] = Field(
        description="Holgura como porcentaje del radio F1. "
        ">=60% cumple el criterio ITU-R P.530, <0% significa línea de vista bloqueada. "
        "`null` en las antenas mismas, donde el radio F1 es cero y el porcentaje no está definido."
    )


class VeredictoSalida(BaseModel):
    """Diagnóstico global, determinado por el punto más crítico del trayecto."""

    estado: str = Field(description="VIABLE, INVASION_FRESNEL o LOS_BLOQUEADA.")
    distancia_punto_critico_m: float
    latitud_critica: float
    longitud_critica: float
    despeje_pct_critico: float
    mensaje: str = Field(description="Mensaje en español listo para mostrar al usuario.")


class RespuestaAnalisis(BaseModel):
    """Resultado completo de analizar un enlace."""

    distancia_total_m: float
    longitud_onda_m: float
    radio_f1_maximo_m: float = Field(description="Radio de F1 en el punto medio del enlace.")
    elevacion_msnm_a: float
    elevacion_msnm_b: float
    altura_efectiva_a_m: float = Field(description="Elevación MSNM de A + altura de su torre.")
    altura_efectiva_b_m: float = Field(description="Elevación MSNM de B + altura de su torre.")
    muestras: List[MuestraAnalisisSalida]
    veredicto: VeredictoSalida
    advertencia: Optional[str] = Field(
        default=None,
        description="Advertencia no bloqueante (por ejemplo, distancia inusualmente grande).",
    )


class ResumenDemo(BaseModel):
    """Metadatos de un caso demo precargado, para listarlo en la interfaz."""

    id: str
    nombre: str
    descripcion: str


class DemoCompleto(ResumenDemo):
    """Un caso demo con los parámetros listos para enviar a /api/analizar."""

    parametros: SolicitudAnalisis


# --- Aplicación ---


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # `_precargar_elevaciones_demos` se define más abajo en este mismo
    # módulo; para cuando esto se ejecuta de verdad (al arrancar el
    # servidor) el módulo ya terminó de cargar por completo, así que el
    # nombre existe. Ver esa función para el porqué (sección 7.3 de la
    # especificación: los demos deben responder sin red).
    _precargar_elevaciones_demos()
    yield


app = FastAPI(
    title="Diagnóstico de Primera Zona de Fresnel",
    description=(
        "Diagnostica si el terreno entre dos antenas invade la primera zona "
        "de Fresnel del enlace, a partir de la ubicación y altura de torre "
        "de cada antena y la frecuencia de operación."
    ),
    version="0.1.0",
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def _manejar_error_validacion(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Traduce los errores de validación de Pydantic a 400 con mensaje en
    español, en vez del 422 genérico por defecto de FastAPI."""
    primer_error = exc.errors()[0]
    campo = ".".join(str(parte) for parte in primer_error["loc"] if parte != "body")
    tipo = primer_error["type"]

    if campo == "frecuencia_valor" and tipo in ("greater_than", "greater_than_equal"):
        mensaje = "la frecuencia debe ser mayor que cero"
    elif campo.endswith("latitud"):
        mensaje = "la latitud debe estar entre -90 y 90 grados"
    elif campo.endswith("longitud"):
        mensaje = "la longitud debe estar entre -180 y 180 grados"
    elif campo.endswith("altura_torre_m"):
        mensaje = "la altura de la torre no puede ser negativa"
    elif campo == "k":
        mensaje = "el factor k debe ser un número positivo (valor típico: 4/3)"
    elif campo == "criterio_despeje_pct":
        mensaje = "el criterio de despeje debe estar entre 0 y 100 %"
    else:
        mensaje = f"dato inválido en '{campo}': {primer_error['msg']}"

    return JSONResponse(status_code=400, content={"detail": mensaje})


@app.exception_handler(Exception)
async def _manejar_error_inesperado(request: Request, exc: Exception) -> JSONResponse:
    """Red de seguridad final: sin esto, cualquier excepción no prevista
    (por ejemplo, un ValueError interno de core/ que ningún except
    específico esperaba) la devolvería FastAPI como un 500 genérico en
    inglés ("Internal Server Error"), sin traducir. No se incluye el
    texto de `exc` ni ningún traceback en la respuesta — eso son detalles
    internos, no un mensaje accionable para quien usa la app."""
    return JSONResponse(
        status_code=500,
        content={
            "detail": (
                "Ocurrió un error inesperado en el servidor al procesar la "
                "solicitud. Intenta de nuevo; si persiste, revisa que las "
                "coordenadas y la frecuencia sean válidas."
            )
        },
    )


@app.post(
    "/api/analizar",
    response_model=RespuestaAnalisis,
    summary="Diagnostica la primera zona de Fresnel de un enlace",
    tags=["análisis"],
)
def analizar(solicitud: SolicitudAnalisis) -> RespuestaAnalisis:
    """Calcula el perfil de terreno y la geometría de Fresnel del enlace,
    y devuelve el veredicto de despeje.

    Llama a la API de elevación **una sola vez** por análisis (para todo el
    perfil muestreado); cambiar la frecuencia o las alturas de torre sin
    mover los puntos A/B se recalcula en el navegador reutilizando este
    mismo perfil (ver Contexto/CLAUDE.md, decisión 1-2), no vuelve a llamar
    a este endpoint.
    """
    a = solicitud.punto_a
    b = solicitud.punto_b

    if a.latitud == b.latitud and a.longitud == b.longitud:
        raise HTTPException(
            status_code=400,
            detail="Los puntos A y B son el mismo lugar; no hay enlace que analizar.",
        )

    frecuencia_hz = _frecuencia_a_hz(solicitud.frecuencia_valor, solicitud.frecuencia_unidad)

    distancia_total_m = distancia_geodesica(a.latitud, a.longitud, b.latitud, b.longitud)
    advertencia = None
    if distancia_total_m > _DISTANCIA_ADVERTENCIA_M:
        advertencia = (
            f"La distancia entre A y B es de {distancia_total_m / 1000:.1f} km, "
            "más de 200 km: el modelo de radioenlace terrestre deja de ser "
            "realista a esta escala, aunque el cálculo se completó."
        )

    n_muestras = numero_muestras_recomendado(distancia_total_m)
    puntos_geo = muestrear_trayecto(a.latitud, a.longitud, b.latitud, b.longitud, n_muestras)

    try:
        elevaciones = fuente_elevacion.consultar(
            [PuntoConsulta(p.latitud, p.longitud) for p in puntos_geo]
        )
    except ErrorLimiteTasa as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "La API de elevación aplicó su límite de tasa de consultas; "
                f"espera unos segundos y vuelve a intentar. Detalle: {exc}"
            ),
        ) from exc
    except ErrorRed as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "No se pudo contactar la API de elevación (problema de red o "
                f"del servidor remoto). Detalle: {exc}"
            ),
        ) from exc
    except ErrorSinDatosDeElevacion as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "No hay datos de elevación para uno o más puntos del trayecto: "
                "puede que el enlace cruce el mar, o que alguno de los puntos "
                "esté fuera de la cobertura del dataset SRTM (~60° de latitud "
                f"norte/sur). Verifica las coordenadas de A y B. Detalle: {exc}"
            ),
        ) from exc
    except ErrorRespuestaInvalida as exc:
        raise HTTPException(
            status_code=502,
            detail=f"La API de elevación devolvió una respuesta inválida. Detalle: {exc}",
        ) from exc

    perfil = [
        MuestraTerreno(
            latitud=p.latitud,
            longitud=p.longitud,
            distancia_acumulada_m=p.distancia_acumulada_m,
            elevacion_msnm=elevacion,
        )
        for p, elevacion in zip(puntos_geo, elevaciones)
    ]

    resultado = analizar_enlace(
        perfil,
        elevacion_msnm_a=elevaciones[0],
        altura_torre_a_m=a.altura_torre_m,
        elevacion_msnm_b=elevaciones[-1],
        altura_torre_b_m=b.altura_torre_m,
        frecuencia_hz=frecuencia_hz,
        k=solicitud.k,
        criterio_despeje_pct=solicitud.criterio_despeje_pct,
    )

    return RespuestaAnalisis(
        distancia_total_m=resultado.distancia_total_m,
        longitud_onda_m=resultado.longitud_onda_m,
        radio_f1_maximo_m=resultado.radio_f1_maximo_m,
        elevacion_msnm_a=elevaciones[0],
        elevacion_msnm_b=elevaciones[-1],
        altura_efectiva_a_m=resultado.altura_efectiva_a_m,
        altura_efectiva_b_m=resultado.altura_efectiva_b_m,
        muestras=[
            MuestraAnalisisSalida(
                latitud=m.latitud,
                longitud=m.longitud,
                distancia_acumulada_m=m.distancia_acumulada_m,
                radio_f1_m=m.radio_f1_m,
                abultamiento_m=m.abultamiento_m,
                altura_linea_vista_m=m.altura_linea_vista_m,
                holgura_m=m.holgura_m,
                despeje_pct=m.despeje_pct,
            )
            for m in resultado.muestras
        ],
        veredicto=VeredictoSalida(
            estado=resultado.veredicto.estado.value,
            distancia_punto_critico_m=resultado.veredicto.distancia_punto_critico_m,
            latitud_critica=resultado.veredicto.latitud_critica,
            longitud_critica=resultado.veredicto.longitud_critica,
            despeje_pct_critico=resultado.veredicto.despeje_pct_critico,
            mensaje=resultado.veredicto.mensaje,
        ),
        advertencia=advertencia,
    )


def _cargar_demos() -> dict:
    """Lee los casos demo desde `data/demos/*.json`.

    Se relee del disco en cada llamada en vez de cachear en memoria: son
    pocos archivos pequeños y así un caso agregado o corregido en disco se
    refleja sin reiniciar el servidor. El campo `elevaciones` de cada
    archivo no se expone aquí (ver `_precargar_elevaciones_demos`): son las
    elevaciones congeladas del perfil, no parte de la respuesta pública.
    """
    demos: dict = {}
    if not _DIRECTORIO_DEMOS.exists():
        return demos
    for archivo in sorted(_DIRECTORIO_DEMOS.glob("*.json")):
        datos = json.loads(archivo.read_text(encoding="utf-8"))
        demo = DemoCompleto(
            id=datos["id"],
            nombre=datos["nombre"],
            descripcion=datos["descripcion"],
            parametros=datos["parametros"],
        )
        demos[demo.id] = demo
    return demos


def _precargar_elevaciones_demos() -> None:
    """Inyecta en la caché de elevaciones en memoria las elevaciones
    congeladas de cada caso demo (generadas por
    `scripts/generar_demos.py`), para que /api/analizar responda a los
    tres casos demo sin red aunque la API de elevación no esté disponible
    (sección 7.3 de la especificación)."""
    if not _DIRECTORIO_DEMOS.exists():
        return
    for archivo in sorted(_DIRECTORIO_DEMOS.glob("*.json")):
        datos = json.loads(archivo.read_text(encoding="utf-8"))
        elevaciones = datos.get("elevaciones")
        if elevaciones:
            fuente_elevacion.precargar(elevaciones)


@app.get(
    "/api/demos",
    response_model=List[ResumenDemo],
    summary="Lista los casos demo precargados",
    tags=["demos"],
)
def listar_demos() -> List[ResumenDemo]:
    """Casos reales precargados con fines de sustentación (enlace despejado,
    obstruido y marginal). Ver Contexto/PROYECTO-FRESNEL.md, sección 7.1."""
    return list(_cargar_demos().values())


@app.get(
    "/api/demos/{demo_id}",
    response_model=DemoCompleto,
    summary="Obtiene los parámetros de un caso demo",
    tags=["demos"],
)
def obtener_demo(demo_id: str) -> DemoCompleto:
    demos = _cargar_demos()
    if demo_id not in demos:
        raise HTTPException(
            status_code=404,
            detail=f"No existe un caso demo con id '{demo_id}'.",
        )
    return demos[demo_id]


# Se monta al final: los endpoints /api/... y /docs se registran primero y
# tienen prioridad; esto solo sirve lo que no matchee ninguna ruta explícita.
app.mount("/", StaticFiles(directory=str(_DIRECTORIO_WEB), html=True), name="web")
