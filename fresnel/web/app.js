/**
 * Aplicación del diagnóstico de primera zona de Fresnel.
 *
 * Arquitectura del recálculo en vivo (ver Contexto/CLAUDE.md, decisión
 * 1-2): /api/analizar se llama SOLO cuando cambian los puntos A/B (porque
 * eso cambia el trayecto geodésico y exige nueva elevación del terreno).
 * A partir de esa respuesta se reconstruye el perfil crudo (lat, lon,
 * distancia, elevación real) y se cachea en `estado.perfil`. Mover la
 * frecuencia, las alturas de torre, k o el criterio de despeje SOLO llama
 * a FresnelCore.analizarEnlace() (web/fresnel.js) sobre ese perfil ya
 * conocido — puro JavaScript, sin red, así de fluido como se pueda mover
 * el control.
 */

(function () {
  "use strict";

  // ---------------------------------------------------------------------
  // Colores leídos de style.css (una sola fuente de verdad para la
  // paleta): el gráfico Plotly no puede referenciar var(--...) en sus
  // propios atributos de color, así que se leen una vez aquí.
  // ---------------------------------------------------------------------
  const raiz = getComputedStyle(document.documentElement);
  const token = (nombre) => raiz.getPropertyValue(nombre).trim();

  const COLOR_TEXTO = token("--color-text") || "#201e1d";
  const COLOR_BG = token("--color-bg") || "#f3f2f2";
  const COLOR_DIVISOR = "#c9c6c6";

  const ESTADO_COLOR = {
    VIABLE: { linea: token("--color-ok") || "#3d6470", relleno: "rgba(61,100,112,0.20)" },
    INVASION_FRESNEL: { linea: token("--color-accent") || "#ec3013", relleno: "rgba(236,48,19,0.18)" },
    LOS_BLOQUEADA: { linea: token("--color-bloqueada") || "#7c1405", relleno: "rgba(124,20,5,0.22)" },
  };
  const ESTADO_ETIQUETA = {
    VIABLE: "VIABLE",
    INVASION_FRESNEL: "INVASIÓN DE FRESNEL",
    LOS_BLOQUEADA: "LÍNEA DE VISTA BLOQUEADA",
  };
  const ESTADO_KICKER_FONDO = {
    VIABLE: token("--color-ok-bg") || "#e2ebee",
    INVASION_FRESNEL: token("--color-invasion-bg") || "#fff2ef",
    LOS_BLOQUEADA: token("--color-bloqueada-bg") || "#f6e4e0",
  };

  const RULER_MIN_MHZ = 400;
  const RULER_MAX_MHZ = 40000;
  const DEBOUNCE_PUNTOS_MS = 500;

  // ---------------------------------------------------------------------
  // Estado de la aplicación
  // ---------------------------------------------------------------------
  const estado = {
    puntoA: { lat: 4.6588, lon: -74.0937, alturaTorreM: 40 },
    puntoB: { lat: 4.8114, lon: -74.1015, alturaTorreM: 40 },
    frecuenciaHz: 5.8e9,
    k: 4 / 3,
    criterioDespejePct: 60,
    perfil: null, // null hasta que llegue la primera respuesta de /api/analizar
    elevacionMsnmA: null,
    elevacionMsnmB: null,
    mapaObjetivo: "A",
    ultimoResultado: null,
  };

  let mapa, marcadorA, marcadorB, lineaEnlace, tileLayer;
  let temporizadorDebounce = null;
  let arrastrandoRuler = false;

  // ---------------------------------------------------------------------
  // Estados de la interfaz: carga / error / entradas inválidas
  // ---------------------------------------------------------------------
  function mostrarBanner(texto, tipo, onReintentar) {
    const banner = document.getElementById("banner-estado");
    const textoEl = document.getElementById("banner-estado-texto");
    const btnReintentar = document.getElementById("banner-estado-reintentar");
    textoEl.textContent = texto;
    banner.dataset.tipo = tipo;
    banner.hidden = false;
    if (onReintentar) {
      btnReintentar.hidden = false;
      btnReintentar.onclick = onReintentar;
    } else {
      btnReintentar.hidden = true;
      btnReintentar.onclick = null;
    }
  }
  function ocultarBanner() {
    document.getElementById("banner-estado").hidden = true;
  }
  function mostrarCarga(texto) {
    mostrarBanner(texto, "carga", null);
  }
  function mostrarError(texto, onReintentar) {
    mostrarBanner(`Error: ${texto}`, "error", onReintentar);
  }

  function mostrarErrorCampo(idError, mensaje) {
    document.getElementById(idError).textContent = mensaje || "";
  }

  /**
   * Traduce un error de JavaScript a un mensaje en español seguro para
   * mostrar al usuario. `fetch()` lanza un `TypeError` nativo del
   * navegador cuando la red falla a ese nivel (sin conexión, DNS, CORS) —
   * su `.message` viene en inglés ("Failed to fetch", "NetworkError
   * when attempting to fetch resource", etc.) y en el idioma del
   * navegador del usuario, nunca en español. Los errores que SÍ lanza
   * esta app (con `throw new Error(...)`, incluidos los de
   * FresnelCore.analizarEnlace) ya están en español y se muestran tal
   * cual.
   */
  function mensajeDeError(err) {
    if (err instanceof TypeError) {
      return "no se pudo conectar con el servidor; revisa tu conexión a internet.";
    }
    return err.message;
  }

  // ---------------------------------------------------------------------
  // Validación de entradas (mismas reglas que la API — ver api/main.py)
  // ---------------------------------------------------------------------
  function leerYValidarPunto(sufijo) {
    const lat = parseFloat(document.getElementById(`lat-${sufijo}`).value);
    const lon = parseFloat(document.getElementById(`lon-${sufijo}`).value);
    const altura = parseFloat(document.getElementById(`altura-${sufijo}`).value);
    const errores = [];

    const latInput = document.getElementById(`lat-${sufijo}`);
    const lonInput = document.getElementById(`lon-${sufijo}`);
    const alturaInput = document.getElementById(`altura-${sufijo}`);
    latInput.removeAttribute("aria-invalid");
    lonInput.removeAttribute("aria-invalid");
    alturaInput.removeAttribute("aria-invalid");

    if (Number.isNaN(lat) || lat < -90 || lat > 90) {
      errores.push("la latitud debe estar entre -90 y 90");
      latInput.setAttribute("aria-invalid", "true");
    }
    if (Number.isNaN(lon) || lon < -180 || lon > 180) {
      errores.push("la longitud debe estar entre -180 y 180");
      lonInput.setAttribute("aria-invalid", "true");
    }
    if (Number.isNaN(altura) || altura < 0) {
      errores.push("la altura de la torre no puede ser negativa");
      alturaInput.setAttribute("aria-invalid", "true");
    }

    mostrarErrorCampo(`error-${sufijo}`, errores.join("; "));
    return { lat, lon, altura, valido: errores.length === 0 };
  }

  function puntosSonElMismoLugar(a, b) {
    return Math.abs(a.lat - b.lat) < 1e-9 && Math.abs(a.lon - b.lon) < 1e-9;
  }

  // ---------------------------------------------------------------------
  // Llamada a /api/analizar — solo cuando A o B cambian
  // ---------------------------------------------------------------------
  async function actualizarPuntos() {
    const a = leerYValidarPunto("a");
    const b = leerYValidarPunto("b");
    if (!a.valido || !b.valido) {
      mostrarError("hay campos con datos inválidos; corrígelos arriba antes de continuar.");
      return;
    }
    if (puntosSonElMismoLugar(a, b)) {
      mostrarError("los puntos A y B son el mismo lugar; no hay enlace que analizar.");
      return;
    }

    estado.puntoA.lat = a.lat;
    estado.puntoA.lon = a.lon;
    estado.puntoA.alturaTorreM = a.altura;
    estado.puntoB.lat = b.lat;
    estado.puntoB.lon = b.lon;
    estado.puntoB.alturaTorreM = b.altura;

    sincronizarMapa();
    mostrarCarga("Consultando la elevación del terreno…");

    try {
      const cuerpo = {
        punto_a: { latitud: a.lat, longitud: a.lon, altura_torre_m: a.altura },
        punto_b: { latitud: b.lat, longitud: b.lon, altura_torre_m: b.altura },
        frecuencia_valor: estado.frecuenciaHz / 1e9,
        frecuencia_unidad: "GHz",
        k: estado.k,
        criterio_despeje_pct: estado.criterioDespejePct,
      };
      const respuesta = await fetch("/api/analizar", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(cuerpo),
      });
      if (!respuesta.ok) {
        const err = await respuesta.json().catch(() => ({}));
        throw new Error(err.detail || `el servidor respondió ${respuesta.status}`);
      }
      const resultado = await respuesta.json();
      cachearPerfilDesdeRespuesta(resultado);
      ocultarBanner();
      recalcularYRenderizar();
    } catch (err) {
      const esFalloDeRed = err instanceof TypeError;
      const mensaje = esFalloDeRed
        ? `${mensajeDeError(err)} Reintenta cuando tengas conexión.`
        : mensajeDeError(err);
      mostrarError(mensaje, actualizarPuntos);
    }
  }

  /**
   * Reconstruye el perfil crudo (con elevación REAL, no corregida por
   * curvatura) a partir de la respuesta de /api/analizar, para poder
   * recalcular después con cualquier frecuencia/k/altura sin volver a
   * pedirlo. abultamiento_m y holgura_m ya vienen calculados con el k y
   * las alturas de ESTA respuesta; se invierte la fórmula para recuperar
   * la elevación cruda, que es la única cantidad que en verdad no cambia.
   */
  function cachearPerfilDesdeRespuesta(resultado) {
    estado.elevacionMsnmA = resultado.elevacion_msnm_a;
    estado.elevacionMsnmB = resultado.elevacion_msnm_b;
    estado.perfil = resultado.muestras.map((m) => ({
      latitud: m.latitud,
      longitud: m.longitud,
      distancia_acumulada_m: m.distancia_acumulada_m,
      elevacion_msnm: m.altura_linea_vista_m - m.holgura_m - m.abultamiento_m,
    }));
  }

  function solicitarActualizacionConDebounce() {
    if (temporizadorDebounce) clearTimeout(temporizadorDebounce);
    temporizadorDebounce = setTimeout(actualizarPuntos, DEBOUNCE_PUNTOS_MS);
  }

  // ---------------------------------------------------------------------
  // Recálculo puro en cliente (frecuencia, alturas, k, criterio)
  // ---------------------------------------------------------------------
  function recalcularYRenderizar() {
    if (!estado.perfil) return;

    let resultado;
    try {
      resultado = FresnelCore.analizarEnlace(
        estado.perfil,
        estado.elevacionMsnmA,
        estado.puntoA.alturaTorreM,
        estado.elevacionMsnmB,
        estado.puntoB.alturaTorreM,
        estado.frecuenciaHz,
        estado.k,
        estado.criterioDespejePct
      );
    } catch (err) {
      mostrarError(mensajeDeError(err));
      return;
    }

    estado.ultimoResultado = resultado;
    renderPerfilFresnel(resultado, estado.criterioDespejePct, "grafico");
    renderVeredicto(resultado.veredicto);
    renderTablaValores(resultado);
    renderResumenCabecera(resultado);
  }

  // ---------------------------------------------------------------------
  // Render: veredicto
  // ---------------------------------------------------------------------
  function renderVeredicto(veredicto) {
    const color = ESTADO_COLOR[veredicto.estado] || ESTADO_COLOR.VIABLE;
    const panel = document.getElementById("veredicto");
    panel.style.borderLeftColor = color.linea;

    const kicker = document.getElementById("veredicto-kicker");
    kicker.style.color = color.linea;

    const titulo = document.getElementById("veredicto-titulo");
    titulo.textContent = ESTADO_ETIQUETA[veredicto.estado] || veredicto.estado;
    titulo.style.color = color.linea;

    document.getElementById("veredicto-despeje").innerHTML =
      `${veredicto.despeje_pct_critico.toFixed(1)}<span class="unidad">%</span>`;
    document.getElementById("veredicto-despeje").style.color = color.linea;
    document.getElementById("veredicto-km").innerHTML =
      `${(veredicto.distancia_punto_critico_m / 1000).toFixed(2)}<span class="unidad">km</span>`;

    document.getElementById("veredicto-explicacion").textContent = veredicto.mensaje;
  }

  // ---------------------------------------------------------------------
  // Render: tabla de valores intermedios (sección 7.2 de la especificación)
  // ---------------------------------------------------------------------
  function renderTablaValores(resultado) {
    const filas = [
      ["Distancia del enlace", (resultado.distancia_total_m / 1000).toFixed(2), "km"],
      ["Longitud de onda", (resultado.longitud_onda_m * 100).toFixed(2), "cm"],
      ["Radio máx. de Fresnel (F1)", resultado.radio_f1_maximo_m.toFixed(2), "m"],
      ["Elevación sitio A (MSNM)", resultado.elevacion_msnm_a.toFixed(1), "msnm"],
      ["Elevación sitio B (MSNM)", resultado.elevacion_msnm_b.toFixed(1), "msnm"],
      ["Altura efectiva A", resultado.altura_efectiva_a_m.toFixed(1), "msnm"],
      ["Altura efectiva B", resultado.altura_efectiva_b_m.toFixed(1), "msnm"],
      [
        "Punto crítico (desde A)",
        (resultado.veredicto.distancia_punto_critico_m / 1000).toFixed(2),
        "km",
      ],
      ["Despeje en punto crítico", resultado.veredicto.despeje_pct_critico.toFixed(1), "%"],
      ["Factor k", estado.k.toFixed(2), "—"],
      ["Criterio de despeje", estado.criterioDespejePct.toFixed(0), "%"],
    ];
    const tbody = document.querySelector("#tabla-valores tbody");
    tbody.innerHTML = "";
    for (const [label, valor, unidad] of filas) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${label}</td><td class="num">${valor}</td><td class="unit">${unidad}</td>`;
      tbody.appendChild(tr);
    }
  }

  function renderResumenCabecera(resultado) {
    const distKm = (resultado.distancia_total_m / 1000).toFixed(1);
    const freq = formatoFrecuencia(estado.frecuenciaHz / 1e6);
    document.getElementById("resumen-cabecera").textContent =
      `${distKm} km · ${freq.valor} ${freq.unidad} · ${ESTADO_ETIQUETA[resultado.veredicto.estado]}`;
  }

  // ---------------------------------------------------------------------
  // Gráfico de perfil (Plotly) — mismas 6 capas del prompt anterior,
  // recoloreadas con la paleta del sistema de diseño de referencia.
  // ---------------------------------------------------------------------
  function hexARgba(hex, alpha) {
    const n = parseInt(hex.replace("#", ""), 16);
    const r = (n >> 16) & 255,
      g = (n >> 8) & 255,
      b = n & 255;
    return `rgba(${r},${g},${b},${alpha})`;
  }

  function encontrarPuntoCritico(muestras) {
    let critica = null;
    for (const m of muestras) {
      if (m.despeje_pct === null || m.despeje_pct === undefined) continue;
      if (critica === null || m.despeje_pct < critica.despeje_pct) critica = m;
    }
    return critica;
  }

  const TITULO_BASE = "Perfil del enlace y primera zona de Fresnel";
  function tituloConExageracion(texto) {
    return `${TITULO_BASE}<br><span style="font-size:11px;color:${COLOR_TEXTO};opacity:0.6">${texto}</span>`;
  }

  function construirTrazas(resultado, criterioDespejePct) {
    const muestras = resultado.muestras;
    const distKm = muestras.map((m) => m.distancia_acumulada_m / 1000);
    const terreno = muestras.map((m) => m.altura_linea_vista_m - m.holgura_m);
    const los = muestras.map((m) => m.altura_linea_vista_m);

    const f1Superior = muestras.map((m) => m.altura_linea_vista_m + m.radio_f1_m);
    const f1Inferior = muestras.map((m) => m.altura_linea_vista_m - m.radio_f1_m);

    const factorCriterio = criterioDespejePct / 100;
    const critSuperior = muestras.map((m) => m.altura_linea_vista_m + factorCriterio * m.radio_f1_m);
    const critInferior = muestras.map((m) => m.altura_linea_vista_m - factorCriterio * m.radio_f1_m);

    const colorEstado = ESTADO_COLOR[resultado.veredicto.estado] || ESTADO_COLOR.VIABLE;

    const minY = Math.min(...terreno, ...f1Inferior);
    const maxY = Math.max(...terreno, ...f1Superior);
    const margenY = (maxY - minY) * 0.12 || 5;
    const baseY = minY - margenY;

    const trazaBase = {
      x: distKm,
      y: distKm.map(() => baseY),
      mode: "lines",
      line: { width: 0 },
      hoverinfo: "skip",
      showlegend: false,
    };

    // Terreno: silueta sólida en el color de texto (como el diseño de
    // referencia), no un marrón "de mapa" — este instrumento usa una sola
    // familia de acento (rojo) y reserva el color con fuerza semántica
    // para el veredicto.
    const trazaTerreno = {
      x: distKm,
      y: terreno,
      name: "Terreno (corregido por curvatura)",
      mode: "lines",
      line: { color: COLOR_TEXTO, width: 1.5 },
      fill: "tonexty",
      fillcolor: COLOR_TEXTO,
      hovertemplate: "Terreno: %{y:.1f} msnm<extra></extra>",
    };

    const infoRadioDespeje = muestras.map((m) => [
      m.radio_f1_m,
      m.despeje_pct === null || m.despeje_pct === undefined
        ? "N/D (en la antena, radio F1 = 0)"
        : `${m.despeje_pct.toFixed(1)}%`,
    ]);

    const trazaF1Superior = {
      x: distKm,
      y: f1Superior,
      name: "Zona de Fresnel (F1)",
      mode: "lines",
      line: { color: colorEstado.linea, width: 1 },
      customdata: infoRadioDespeje,
      hovertemplate: "Radio F1: %{customdata[0]:.1f} m<br>Despeje: %{customdata[1]}<extra></extra>",
    };
    const trazaF1Inferior = {
      x: distKm,
      y: f1Inferior,
      name: "Zona de Fresnel (F1)",
      showlegend: false,
      mode: "lines",
      line: { color: colorEstado.linea, width: 1 },
      fill: "tonexty",
      fillcolor: colorEstado.relleno,
      hoverinfo: "skip",
    };

    const nombreCriterio = `Criterio de despeje (${criterioDespejePct}% de F1)`;
    const estiloLineaCriterio = { color: COLOR_TEXTO, width: 1.5, dash: "dash" };
    const trazaCritSuperior = {
      x: distKm,
      y: critSuperior,
      name: nombreCriterio,
      mode: "lines",
      line: estiloLineaCriterio,
      hoverinfo: "skip",
    };
    const trazaCritInferior = {
      x: distKm,
      y: critInferior,
      name: nombreCriterio,
      showlegend: false,
      mode: "lines",
      line: estiloLineaCriterio,
      hoverinfo: "skip",
    };

    // Línea de vista con halo claro para que se lea limpia sobre la
    // silueta oscura del terreno (recurso tomado del diseño de referencia).
    const trazaLosHalo = {
      x: distKm,
      y: los,
      name: "Línea de vista",
      showlegend: false,
      mode: "lines",
      line: { color: COLOR_BG, width: 5 },
      hoverinfo: "skip",
    };
    const trazaLos = {
      x: distKm,
      y: los,
      name: "Línea de vista",
      mode: "lines",
      line: { color: COLOR_TEXTO, width: 2 },
      hovertemplate: "Línea de vista: %{y:.1f} msnm<extra></extra>",
    };

    const distTotalKm = resultado.distancia_total_m / 1000;
    const trazaTorres = {
      x: [0, 0, null, distTotalKm, distTotalKm],
      y: [
        terreno[0],
        resultado.altura_efectiva_a_m,
        null,
        terreno[terreno.length - 1],
        resultado.altura_efectiva_b_m,
      ],
      name: "Torres",
      mode: "lines",
      line: { color: COLOR_TEXTO, width: 5 },
      hoverinfo: "skip",
    };

    const critica = encontrarPuntoCritico(muestras);
    const trazas = [
      trazaBase,
      trazaTerreno,
      trazaF1Superior,
      trazaF1Inferior,
      trazaCritSuperior,
      trazaCritInferior,
      trazaLosHalo,
      trazaLos,
      trazaTorres,
    ];

    if (critica) {
      trazas.push({
        x: [critica.distancia_acumulada_m / 1000],
        y: [critica.altura_linea_vista_m - critica.holgura_m],
        name: "Punto crítico",
        mode: "markers",
        marker: {
          size: 12,
          color: colorEstado.linea,
          symbol: "diamond",
          line: { color: COLOR_TEXTO, width: 1.5 },
        },
        hovertemplate: `Punto crítico: %{x:.2f} km<br>Despeje: ${critica.despeje_pct.toFixed(1)}%<extra></extra>`,
      });
    }

    return { trazas, critica, color: colorEstado.linea, minY, maxY };
  }

  function construirAnotaciones(resultado, critica, color, minY, maxY) {
    const anotaciones = [];
    if (critica) {
      const yCritico = critica.altura_linea_vista_m - critica.holgura_m;
      const enMitadSuperior = yCritico > (minY + maxY) / 2;
      anotaciones.push({
        x: critica.distancia_acumulada_m / 1000,
        y: yCritico,
        xref: "x",
        yref: "y",
        text: `${ESTADO_ETIQUETA[resultado.veredicto.estado]}<br>${critica.despeje_pct.toFixed(1)}% de F1`,
        showarrow: true,
        arrowhead: 3,
        arrowcolor: color,
        ax: 0,
        ay: enMitadSuperior ? 50 : -50,
        bgcolor: COLOR_BG,
        bordercolor: color,
        borderwidth: 1.5,
        font: { color: COLOR_TEXTO, size: 12 },
      });
    }
    return anotaciones;
  }

  function actualizarExageracionVertical(contenedor) {
    const fl = contenedor._fullLayout;
    if (!fl || !fl._size) return;

    const xRangeKm = fl.xaxis.range;
    const yRange = fl.yaxis.range;
    const anchoPx = fl._size.w;
    const altoPx = fl._size.h;
    if (!anchoPx || !altoPx) return;

    const metrosPorPixelX = ((xRangeKm[1] - xRangeKm[0]) * 1000) / anchoPx;
    const metrosPorPixelY = (yRange[1] - yRange[0]) / altoPx;
    const exageracion = metrosPorPixelY > 0 ? metrosPorPixelX / metrosPorPixelY : 1;

    // En pantallas angostas la versión completa se corta contra el borde
    // del panel; se acorta el texto pero nunca se omite el dato en sí.
    const texto =
      anchoPx < 380
        ? `Escala vertical exagerada ≈${exageracion.toFixed(0)}× (no está a proporción real)`
        : `Escala vertical exagerada ≈${exageracion.toFixed(0)}× respecto a la horizontal ` +
          "(eje Y en metros, eje X en kilómetros): el relieve NO está a proporción real.";

    const tituloNuevo = tituloConExageracion(texto);
    if (contenedor.layout.title && contenedor.layout.title.text === tituloNuevo) return;
    Plotly.relayout(contenedor, { "title.text": tituloNuevo });
  }

  function renderPerfilFresnel(resultado, criterioDespejePct, contenedorId) {
    const { trazas, critica, color, minY, maxY } = construirTrazas(resultado, criterioDespejePct);
    const anotaciones = construirAnotaciones(resultado, critica, color, minY, maxY);
    const distTotalKm = resultado.distancia_total_m / 1000;

    const layout = {
      title: { text: tituloConExageracion("calculando escala vertical…"), font: { size: 15 } },
      font: { family: "Archivo, system-ui, sans-serif", color: COLOR_TEXTO },
      xaxis: {
        title: "Distancia desde A (km)",
        range: [-distTotalKm * 0.02, distTotalKm * 1.02],
        zeroline: false,
        gridcolor: COLOR_DIVISOR,
      },
      yaxis: { title: "Elevación (msnm)", zeroline: false, gridcolor: COLOR_DIVISOR },
      hovermode: "x unified",
      hoverlabel: { bgcolor: COLOR_BG, font: { color: COLOR_TEXTO } },
      legend: { orientation: "h", y: -0.22 },
      margin: { t: 60, r: 10, b: 80, l: 55 },
      annotations: anotaciones,
      paper_bgcolor: "transparent",
      plot_bgcolor: COLOR_BG,
    };

    const contenedor = document.getElementById(contenedorId);
    Plotly.react(contenedor, trazas, layout, { responsive: true, displaylogo: false }).then(() =>
      actualizarExageracionVertical(contenedor)
    );

    if (!contenedor.__exageracionWireada) {
      contenedor.__exageracionWireada = true;
      window.addEventListener("resize", () => actualizarExageracionVertical(contenedor));
      contenedor.on("plotly_relayout", () => actualizarExageracionVertical(contenedor));
    }
  }

  // ---------------------------------------------------------------------
  // Control de frecuencia: regla logarítmica 400 MHz – 40 GHz + presets
  // ---------------------------------------------------------------------
  function formatoFrecuencia(mhz) {
    if (mhz >= 1000) return { valor: (mhz / 1000).toFixed(1).replace(".", ","), unidad: "GHz" };
    return { valor: Math.round(mhz).toString(), unidad: "MHz" };
  }

  function frecuenciaMhzAPct(mhz) {
    const lo = Math.log(RULER_MIN_MHZ);
    const hi = Math.log(RULER_MAX_MHZ);
    const p = (Math.log(mhz) - lo) / (hi - lo);
    return Math.max(0, Math.min(1, p)) * 100;
  }
  function pctAFrecuenciaMhz(pct) {
    const lo = Math.log(RULER_MIN_MHZ);
    const hi = Math.log(RULER_MAX_MHZ);
    return Math.exp(lo + (pct / 100) * (hi - lo));
  }

  function actualizarUIFrecuencia() {
    const mhz = estado.frecuenciaHz / 1e6;
    const { valor, unidad } = formatoFrecuencia(mhz);
    document.getElementById("freq-valor").textContent = valor;
    document.getElementById("freq-unidad").textContent = unidad;

    const pct = frecuenciaMhzAPct(mhz);
    document.getElementById("ruler-fader").style.left = `${pct}%`;
    const lens = document.getElementById("ruler-lens");
    lens.style.left = `${pct}%`;
    lens.textContent = `${valor} ${unidad}`;

    const track = document.getElementById("ruler-track");
    track.setAttribute("aria-valuenow", Math.round(mhz));
    track.setAttribute("aria-valuetext", `${valor} ${unidad}`);

    document.getElementById("freq-custom").value = Math.round(mhz);

    document.querySelectorAll("[data-preset]").forEach((btn) => {
      const p = parseFloat(btn.getAttribute("data-preset"));
      btn.setAttribute("aria-pressed", Math.abs(p - mhz) < 1 ? "true" : "false");
    });
  }

  function establecerFrecuencia(hz) {
    const hzMin = RULER_MIN_MHZ * 1e6;
    const hzMax = RULER_MAX_MHZ * 1e6;
    estado.frecuenciaHz = Math.max(hzMin, Math.min(hzMax, hz));
    mostrarErrorCampo("error-freq", "");
    actualizarUIFrecuencia();
    recalcularYRenderizar();
  }

  function actualizarFrecuenciaDesdeClientX(clientX) {
    const el = document.getElementById("ruler-track");
    const rect = el.getBoundingClientRect();
    let p = (clientX - rect.left) / rect.width;
    p = Math.max(0, Math.min(1, p));
    establecerFrecuencia(pctAFrecuenciaMhz(p * 100) * 1e6);
  }

  function onRulerPointerMove(e) {
    if (arrastrandoRuler) actualizarFrecuenciaDesdeClientX(e.clientX);
  }
  function onRulerPointerUp() {
    arrastrandoRuler = false;
    window.removeEventListener("pointermove", onRulerPointerMove);
    window.removeEventListener("pointerup", onRulerPointerUp);
  }
  function onRulerPointerDown(e) {
    e.preventDefault();
    arrastrandoRuler = true;
    document.getElementById("ruler-track").focus();
    actualizarFrecuenciaDesdeClientX(e.clientX);
    window.addEventListener("pointermove", onRulerPointerMove);
    window.addEventListener("pointerup", onRulerPointerUp);
  }

  // ---------------------------------------------------------------------
  // Mapa (Leaflet)
  // ---------------------------------------------------------------------
  function inicializarMapa() {
    mapa = L.map("mapa", { attributionControl: false, zoomControl: true }).setView(
      [estado.puntoA.lat, estado.puntoA.lon],
      11
    );
    tileLayer = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
      attribution: "&copy; OpenStreetMap",
    });
    tileLayer.addTo(mapa);
    // El mapa es secundario (sitúa, no diagnostica): si los tiles no
    // cargan por falta de red, marcadores y línea siguen funcionando
    // sobre un fondo en blanco.
    tileLayer.on("tileerror", () => {
      // No pisa un banner ya visible: si el análisis también falló (el
      // caso más importante), ese mensaje no debe desaparecer detrás de
      // este, que es secundario (el mapa solo sitúa, no diagnostica).
      if (!document.getElementById("banner-estado").hidden) return;
      mostrarBanner(
        "No se pudieron cargar las imágenes del mapa (sin conexión); el análisis y el gráfico no se ven afectados.",
        "error",
        null
      );
    });

    marcadorA = L.marker([estado.puntoA.lat, estado.puntoA.lon], { draggable: true })
      .addTo(mapa)
      .bindTooltip("A", { permanent: true, direction: "top", className: "tooltip-sitio" });
    marcadorB = L.marker([estado.puntoB.lat, estado.puntoB.lon], { draggable: true })
      .addTo(mapa)
      .bindTooltip("B", { permanent: true, direction: "top", className: "tooltip-sitio" });
    lineaEnlace = L.polyline(
      [
        [estado.puntoA.lat, estado.puntoA.lon],
        [estado.puntoB.lat, estado.puntoB.lon],
      ],
      { color: COLOR_TEXTO, weight: 3 }
    ).addTo(mapa);

    marcadorA.on("dragend", (e) => aplicarPuntoDesdeMapa("a", e.target.getLatLng()));
    marcadorB.on("dragend", (e) => aplicarPuntoDesdeMapa("b", e.target.getLatLng()));

    mapa.on("click", (e) => {
      const sufijo = document.getElementById("mapa-fijar-a").checked ? "a" : "b";
      aplicarPuntoDesdeMapa(sufijo, e.latlng);
    });
  }

  function aplicarPuntoDesdeMapa(sufijo, latlng) {
    const lat = Math.round(latlng.lat * 10000) / 10000;
    const lon = Math.round(latlng.lng * 10000) / 10000;
    document.getElementById(`lat-${sufijo}`).value = lat;
    document.getElementById(`lon-${sufijo}`).value = lon;
    actualizarPuntos();
  }

  function sincronizarMapa() {
    if (!mapa) return;
    marcadorA.setLatLng([estado.puntoA.lat, estado.puntoA.lon]);
    marcadorB.setLatLng([estado.puntoB.lat, estado.puntoB.lon]);
    const puntos = [
      [estado.puntoA.lat, estado.puntoA.lon],
      [estado.puntoB.lat, estado.puntoB.lon],
    ];
    lineaEnlace.setLatLngs(puntos);
    mapa.fitBounds(lineaEnlace.getBounds().pad(0.35));
    document.getElementById("mapa-caption").textContent =
      `A: ${estado.puntoA.lat.toFixed(4)}, ${estado.puntoA.lon.toFixed(4)} — ` +
      `B: ${estado.puntoB.lat.toFixed(4)}, ${estado.puntoB.lon.toFixed(4)}`;
  }

  // ---------------------------------------------------------------------
  // Alturas de torre (gauge visual + recálculo en vivo)
  // ---------------------------------------------------------------------
  const ALTURA_GAUGE_MAX_M = 80;
  function wireAlturaTorre(sufijo, puntoKey) {
    const input = document.getElementById(`altura-${sufijo}`);
    const gauge = document.getElementById(`gauge-${sufijo}`);
    input.addEventListener("input", () => {
      const v = parseFloat(input.value);
      if (Number.isNaN(v) || v < 0) {
        mostrarErrorCampo(`error-${sufijo}`, "la altura de la torre no puede ser negativa");
        input.setAttribute("aria-invalid", "true");
        return;
      }
      input.removeAttribute("aria-invalid");
      mostrarErrorCampo(`error-${sufijo}`, "");
      estado[puntoKey].alturaTorreM = v;
      gauge.style.height = `${Math.min(100, (v / ALTURA_GAUGE_MAX_M) * 100)}%`;
      recalcularYRenderizar();
    });
  }

  // ---------------------------------------------------------------------
  // Wiring general
  // ---------------------------------------------------------------------
  function wireLatLon(sufijo) {
    ["lat", "lon"].forEach((campo) => {
      document.getElementById(`${campo}-${sufijo}`).addEventListener("change", actualizarPuntos);
    });
  }

  function wirePresets() {
    document.querySelectorAll("[data-preset]").forEach((btn) => {
      btn.addEventListener("click", () => {
        establecerFrecuencia(parseFloat(btn.getAttribute("data-preset")) * 1e6);
      });
    });
  }

  function wireFrecuenciaCustom() {
    const input = document.getElementById("freq-custom");
    input.addEventListener("change", () => {
      const mhz = parseFloat(input.value);
      if (Number.isNaN(mhz) || mhz <= 0) {
        mostrarErrorCampo("error-freq", "la frecuencia debe ser mayor que cero");
        return;
      }
      establecerFrecuencia(mhz * 1e6);
    });
  }

  function generarTicksRegla() {
    const track = document.getElementById("ruler-track");
    const referencias = [400, 900, 2400, 5000, 5800, 10000, 20000, 40000];
    for (let mhz = RULER_MIN_MHZ; mhz <= RULER_MAX_MHZ; mhz *= 1.15) {
      const esReferencia = referencias.some((r) => Math.abs(Math.log(r / mhz)) < 0.05);
      const tick = document.createElement("div");
      tick.className = "ruler-tick";
      tick.style.left = `${frecuenciaMhzAPct(mhz)}%`;
      tick.style.height = esReferencia ? "70%" : "35%";
      tick.style.opacity = esReferencia ? "0.5" : "0.22";
      track.appendChild(tick);
    }
  }

  function wireRuler() {
    const track = document.getElementById("ruler-track");
    generarTicksRegla();
    track.addEventListener("pointerdown", onRulerPointerDown);
    track.addEventListener("keydown", (e) => {
      const factor = e.shiftKey ? 1.1 : 1.02;
      if (e.key === "ArrowRight" || e.key === "ArrowUp") {
        establecerFrecuencia(estado.frecuenciaHz * factor);
        e.preventDefault();
      } else if (e.key === "ArrowLeft" || e.key === "ArrowDown") {
        establecerFrecuencia(estado.frecuenciaHz / factor);
        e.preventDefault();
      }
    });
  }

  function wireAvanzado() {
    document.getElementById("param-k").addEventListener("input", (e) => {
      const v = parseFloat(e.target.value);
      if (Number.isNaN(v) || v <= 0 || v > 3) return;
      estado.k = v;
      recalcularYRenderizar();
    });
    document.getElementById("param-criterio").addEventListener("input", (e) => {
      const v = parseFloat(e.target.value);
      if (Number.isNaN(v) || v < 0 || v > 100) return;
      estado.criterioDespejePct = v;
      recalcularYRenderizar();
    });
  }

  function rellenarFormularioDesdeEstado() {
    document.getElementById("lat-a").value = estado.puntoA.lat;
    document.getElementById("lon-a").value = estado.puntoA.lon;
    document.getElementById("altura-a").value = estado.puntoA.alturaTorreM;
    document.getElementById("gauge-a").style.height =
      `${Math.min(100, (estado.puntoA.alturaTorreM / ALTURA_GAUGE_MAX_M) * 100)}%`;
    document.getElementById("lat-b").value = estado.puntoB.lat;
    document.getElementById("lon-b").value = estado.puntoB.lon;
    document.getElementById("altura-b").value = estado.puntoB.alturaTorreM;
    document.getElementById("gauge-b").style.height =
      `${Math.min(100, (estado.puntoB.alturaTorreM / ALTURA_GAUGE_MAX_M) * 100)}%`;
    document.getElementById("param-k").value = estado.k;
    document.getElementById("param-criterio").value = estado.criterioDespejePct;
    actualizarUIFrecuencia();
  }

  async function cargarDemo(id) {
    mostrarCarga(`Cargando caso demo "${id}"…`);
    try {
      const resDemo = await fetch(`/api/demos/${id}`);
      if (!resDemo.ok) throw new Error(`no se pudo cargar el caso demo '${id}' (HTTP ${resDemo.status})`);
      const demo = await resDemo.json();
      const p = demo.parametros;

      estado.puntoA.lat = p.punto_a.latitud;
      estado.puntoA.lon = p.punto_a.longitud;
      estado.puntoA.alturaTorreM = p.punto_a.altura_torre_m;
      estado.puntoB.lat = p.punto_b.latitud;
      estado.puntoB.lon = p.punto_b.longitud;
      estado.puntoB.alturaTorreM = p.punto_b.altura_torre_m;
      estado.k = p.k;
      estado.criterioDespejePct = p.criterio_despeje_pct;
      const factor = { Hz: 1, MHz: 1e6, GHz: 1e9 }[p.frecuencia_unidad] || 1e9;
      estado.frecuenciaHz = p.frecuencia_valor * factor;

      rellenarFormularioDesdeEstado();
      sincronizarMapa();
      await actualizarPuntos();
    } catch (err) {
      mostrarError(mensajeDeError(err), () => cargarDemo(id));
    }
  }

  function wireDemos() {
    document.querySelectorAll("[data-demo]").forEach((btn) => {
      btn.addEventListener("click", () => cargarDemo(btn.getAttribute("data-demo")));
    });
  }

  // ---------------------------------------------------------------------
  // Arranque
  // ---------------------------------------------------------------------
  document.addEventListener("DOMContentLoaded", () => {
    inicializarMapa();
    rellenarFormularioDesdeEstado();
    sincronizarMapa();

    wireLatLon("a");
    wireLatLon("b");
    wireAlturaTorre("a", "puntoA");
    wireAlturaTorre("b", "puntoB");
    wirePresets();
    wireFrecuenciaCustom();
    wireRuler();
    wireAvanzado();
    wireDemos();

    cargarDemo("despejado");
  });

  // Expuesto para depuración manual desde la consola / pruebas.
  window.__fresnelApp = { estado, recalcularYRenderizar, establecerFrecuencia };
})();
