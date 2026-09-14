# Diagnóstico de Primera Zona de Fresnel

Aplicación web que diagnostica si el terreno entre dos antenas invade la
primera zona de Fresnel de un radioenlace punto a punto, a partir de la
ubicación y altura de torre de cada antena y la frecuencia de operación.

Trabajo académico — Ingeniería Telemática, Universidad Distrital Francisco
José de Caldas (asignatura Redes Inalámbricas, docente Marlon Pantiño
Bernal). Autor: Johan Stiven Ventura Vanegas.

## Qué hace

Dadas las coordenadas de dos sitios (A y B), la altura de su torre y la
frecuencia del enlace:

1. Calcula la distancia geodésica real entre A y B (WGS-84).
2. Descarga el perfil de elevación del terreno a lo largo del trayecto
   (API pública de elevación, con caché en disco).
3. Calcula el radio de la primera zona de Fresnel y el abultamiento por
   curvatura terrestre en cada punto del trayecto.
4. Compara el terreno contra la línea de vista directa y contra el
   criterio de despeje (60% de F1, recomendación ITU-R P.530).
5. Emite un veredicto — **VIABLE**, **INVASIÓN DE FRESNEL** o **LÍNEA DE
   VISTA BLOQUEADA** — señalando el punto crítico del trayecto.
6. Dibuja el perfil del terreno con la zona de Fresnel superpuesta, y un
   mapa con los dos sitios y el enlace.

El control de frecuencia (y las alturas de torre, k, y el criterio de
despeje) recalculan **en vivo, en el navegador**, sin volver a consultar
el servidor — solo cambiar los puntos A o B dispara una nueva consulta de
elevación. Ver [Arquitectura](#arquitectura-y-decisiones-de-diseño).

Trae tres casos demo con ubicaciones reales de Cundinamarca/Bogotá y
elevaciones precacheadas, para que funcionen sin conexión a internet.

## Instalación

Requiere Python 3.9+.

```bash
cd fresnel
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Las dependencias son exactamente estas seis, sin nada más:
`fastapi`, `uvicorn`, `pyproj`, `numpy`, `httpx`, `pytest`.

El frontend no tiene build step ni `node_modules`: es HTML/CSS/JS plano.
Sus dependencias (Leaflet, Plotly, la tipografía Archivo) ya vienen
vendorizadas en `web/vendor/` — no dependen de ningún CDN en producción,
así que no hace falta descargar nada aparte para correr la app (los
tiles del mapa de OpenStreetMap sí requieren red; ver
[Limitaciones](#limitaciones-conocidas)).

## Cómo correr

```bash
source .venv/bin/activate
uvicorn api.main:app --reload
```

Abre `http://127.0.0.1:8000/` para la interfaz, o
`http://127.0.0.1:8000/docs` para la documentación interactiva de la API
(Swagger UI, generada automáticamente por FastAPI — se puede probar
`/api/analizar` directamente ahí, con un ejemplo precargado).

## Tests

```bash
pytest                                        # 59 tests
pytest --cov=core --cov-report=term-missing   # cobertura de core/: 99%
```

`core/` (la capa pura, sin red ni HTTP) tiene 100% de cobertura en
`fresnel.py` y `geo.py`, y 99% en `elevation.py` (la única línea sin
cubrir es el cuerpo de un método abstracto que Python nunca ejecuta).

Un test (`tests/test_consistencia_js_py.py`) corre Chrome headless para
verificar que `core/fresnel.py` (Python) y `web/fresnel.js` (su espejo en
JavaScript, usado para el recálculo en vivo) dan exactamente el mismo
resultado numérico; se salta automáticamente si no hay Chrome instalado.

## Regenerar los casos demo

```bash
python scripts/generar_demos.py
```

Prueba ubicaciones reales candidatas contra la API de elevación real,
verifica que cada una produce el veredicto buscado (despejado / obstruido
/ marginal), y congela el resultado en `data/demos/*.json`. Requiere red;
no hace falta correrlo para usar la app, los tres casos ya vienen
generados.

## De dónde salen los datos

**Elevación del terreno:** [Open-Topo-Data](https://www.opentopodata.org/)
(endpoint público, dataset **SRTM 30 m**, sin necesidad de API key).
Límites del servicio: ~100 ubicaciones por llamada, ~1 llamada por
segundo — ambos respetados explícitamente en `core/elevation.py`
(agrupado en lotes, con espera entre llamadas y reintentos con backoff
exponencial ante errores transitorios).

Las consultas se cachean en disco (`cache/elevaciones.json`, JSON legible,
no un pickle) para no repetir llamadas y para que la app siga
respondiendo si la red falla a mitad de una demostración. Los tres casos
demo traen sus elevaciones ya congeladas en `data/demos/*.json` —
versionadas en el repositorio, se cargan en memoria al arrancar el
servidor, y por eso responden sin red.

**Coordenadas y geodesia:** `pyproj` (elipsoide WGS-84) — la misma
referencia que usan GPS y Google Maps. La distancia entre dos puntos se
resuelve con el problema geodésico inverso, y los puntos intermedios del
perfil se muestrean sobre la geodésica real (círculo máximo), no por
interpolación lineal de latitud/longitud.

**Mapa (Leaflet):** tiles de OpenStreetMap, cargados en vivo desde
`tile.openstreetmap.org` — es la única parte de la app que sigue
dependiendo de una red externa en tiempo de ejecución (el mapa solo sitúa
los puntos, no participa del cálculo; si los tiles no cargan, marcadores,
línea, análisis y gráfico siguen funcionando igual).

## Fórmulas implementadas

Todas viven en `core/fresnel.py` (Python, fuente de verdad) y su espejo
`web/fresnel.js` (JavaScript, para el recálculo en vivo — ver
[Arquitectura](#arquitectura-y-decisiones-de-diseño)).

### Radio de la primera zona de Fresnel

$$r_1 = \sqrt{\dfrac{\lambda \cdot d_1 \cdot d_2}{d_1 + d_2}}$$

con $\lambda = c/f$, $d_1$ la distancia desde la antena A hasta el punto
evaluado, y $d_2$ la distancia desde ese punto hasta la antena B (todo en
unidades SI). El radio es máximo en el punto medio del enlace y cero en
las antenas.

### Abultamiento por curvatura terrestre

$$h_{curv} = \dfrac{d_1 \cdot d_2}{12.75 \cdot k} \quad \text{(d en km, h en m)}$$

`k` es el factor de radio terrestre efectivo, para modelar la refracción
atmosférica; `k = 4/3` es el valor estándar de atmósfera normal
(editable en la interfaz).

### Criterio de despeje

Recomendación **[ITU-R P.530](https://www.itu.int/rec/R-REC-P.530/)**
("Propagation data and prediction methods required for the design of
terrestrial line-of-sight systems"): el trayecto debe mantener libre de
obstáculos al menos el **60% del radio de F1** con `k = 4/3` para que la
pérdida por difracción sea despreciable. La app no usa un veredicto
binario obstruido/libre: reporta el porcentaje de despeje en el punto más
crítico del trayecto, y solo clasifica:

- `despeje % ≥ 60` → **VIABLE**
- `0 ≤ despeje % < 60` → **INVASIÓN DE FRESNEL** (zona de Fresnel invadida,
  línea de vista directa aún libre)
- `despeje % < 0` → **LÍNEA DE VISTA BLOQUEADA** (el terreno corta la
  línea directa — la condición más severa, independiente de la
  frecuencia)

El criterio (60% por defecto) y `k` son editables en el panel de
parámetros avanzados de la interfaz.

## Arquitectura y decisiones de diseño

```
fresnel/
├── core/           # geo.py, elevation.py, fresnel.py — puro, sin HTTP
├── api/            # FastAPI: valida, orquesta core/, traduce errores
├── web/            # HTML/CSS/JS plano + Leaflet/Plotly/Archivo vendorizados
├── data/demos/     # 3 casos reales con elevación congelada (versionado)
├── cache/          # caché de elevación en runtime (no versionado)
├── scripts/        # generar_demos.py, verificar_elevacion.py
└── tests/          # pytest
```

**`core/` no sabe nada de HTTP ni de interfaz** (salvo `elevation.py`, que
sí necesita red porque no hay forma de conocer la elevación real sin
consultar una fuente externa). Se puede ejecutar y probar desde consola,
sin servidor.

**Duplicación intencional entre Python y JavaScript:** el slider de
frecuencia es el control más manipulado de la app, y tiene que recalcular
y redibujar de forma instantánea — un round-trip al backend en cada
movimiento se notaría. Por eso `/api/analizar` se llama **una sola vez**
por cada par de puntos A/B (para obtener el perfil de elevación real), y
ese perfil se cachea en el navegador; mover la frecuencia, las alturas de
torre, `k` o el criterio de despeje solo llama a `web/fresnel.js` — el
mismo cálculo de `core/fresnel.py`, reescrito en JavaScript. Ambos
archivos llevan un comentario cruzándose entre sí, y
`tests/test_consistencia_js_py.py` verifica automáticamente que no se
desincronicen.

Más contexto de decisiones de diseño (unidades de frecuencia, mensajes de
error, muestreo adaptativo, etc.) en `Contexto/CLAUDE.md`.

## Limitaciones conocidas

- El dataset SRTM no tiene cobertura sobre el mar ni más allá de ~60° de
  latitud norte/sur; un enlace que caiga ahí falla con un mensaje
  explícito pidiendo revisar las coordenadas, no con un error genérico.
- Más allá de 200 km entre A y B, la app muestra una advertencia: el
  modelo de radioenlace terrestre (línea de vista + Fresnel) deja de ser
  un supuesto realista a esa escala, aunque el cálculo se complete.
- El mapa depende de tiles de OpenStreetMap cargados en vivo; sin red,
  el mapa queda en blanco pero el análisis y el gráfico de perfil no se
  ven afectados (los tres casos demo, con elevación precacheada,
  funcionan sin red incluyendo el análisis).
