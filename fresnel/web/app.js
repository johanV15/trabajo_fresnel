/**
 * Gráfico de perfil del enlace + primera zona de Fresnel, con Plotly.
 *
 * Capas del gráfico, de fondo a frente:
 *   1. Terreno corregido por curvatura (área rellena).
 *   2. Banda de la primera zona de Fresnel (F1), semitransparente,
 *      coloreada según el veredicto (verde/ámbar/rojo).
 *   3. Banda del criterio de despeje (por defecto 60% de F1), como líneas
 *      punteadas oscuras — visualmente distinta de la banda F1 completa.
 *   4. Línea de vista directa entre las dos antenas.
 *   5. Torres, desde el terreno hasta la altura efectiva de cada antena.
 *   6. Marcador + anotación en el punto crítico (menor % de despeje).
 *
 * Colores de estado: paleta de estado fija del sistema de diseño (nunca se
 * reutiliza para otra cosa) — verde=viable, ámbar=invasión, rojo=bloqueada.
 */

(function () {
  "use strict";

  const ESTADO_COLOR = {
    VIABLE: "#0ca30c",
    INVASION_FRESNEL: "#fab219",
    LOS_BLOQUEADA: "#d03b3b",
  };

  const ESTADO_ETIQUETA = {
    VIABLE: "Viable",
    INVASION_FRESNEL: "Invasión de Fresnel",
    LOS_BLOQUEADA: "Línea de vista bloqueada",
  };

  const COLOR_TERRENO_LINEA = "#7a5230";
  const COLOR_TERRENO_FILL = "rgba(122,82,48,0.45)";
  const COLOR_LOS = "#0b0b0b";
  const COLOR_CRITERIO = "#0b0b0b";
  const COLOR_EJES = "#e1e0d9";
  const COLOR_TEXTO_SECUNDARIO = "#52514e";

  function hexARgba(hex, alpha) {
    const n = parseInt(hex.slice(1), 16);
    const r = (n >> 16) & 255;
    const g = (n >> 8) & 255;
    const b = n & 255;
    return `rgba(${r},${g},${b},${alpha})`;
  }

  /** La muestra con menor % de despeje, ignorando los extremos (radio F1 = 0,
   * despeje_pct = null ahí — ver core/fresnel.py). */
  function encontrarPuntoCritico(muestras) {
    let critica = null;
    for (const m of muestras) {
      if (m.despeje_pct === null || m.despeje_pct === undefined) continue;
      if (critica === null || m.despeje_pct < critica.despeje_pct) critica = m;
    }
    return critica;
  }

  function construirTrazas(resultado, criterioDespejePct) {
    const muestras = resultado.muestras;
    const distKm = muestras.map((m) => m.distancia_acumulada_m / 1000);
    // El terreno "corregido por curvatura" es exactamente altura_linea_vista
    // menos la holgura que ya calculó el backend (holgura = LOS - terreno
    // efectivo); se deriva aquí en vez de pedirle este dato al backend
    // porque es una resta exacta, sin pérdida de información.
    const terreno = muestras.map((m) => m.altura_linea_vista_m - m.holgura_m);
    const los = muestras.map((m) => m.altura_linea_vista_m);

    const f1Superior = muestras.map((m) => m.altura_linea_vista_m + m.radio_f1_m);
    const f1Inferior = muestras.map((m) => m.altura_linea_vista_m - m.radio_f1_m);

    const factorCriterio = criterioDespejePct / 100;
    const critSuperior = muestras.map(
      (m) => m.altura_linea_vista_m + factorCriterio * m.radio_f1_m
    );
    const critInferior = muestras.map(
      (m) => m.altura_linea_vista_m - factorCriterio * m.radio_f1_m
    );

    const color = ESTADO_COLOR[resultado.veredicto.estado] || COLOR_TEXTO_SECUNDARIO;

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

    const trazaTerreno = {
      x: distKm,
      y: terreno,
      name: "Terreno (corregido por curvatura)",
      mode: "lines",
      line: { color: COLOR_TERRENO_LINEA, width: 2 },
      fill: "tonexty",
      fillcolor: COLOR_TERRENO_FILL,
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
      line: { color: color, width: 1 },
      customdata: infoRadioDespeje,
      hovertemplate: "Radio F1: %{customdata[0]:.1f} m<br>Despeje: %{customdata[1]}<extra></extra>",
    };
    const trazaF1Inferior = {
      x: distKm,
      y: f1Inferior,
      name: "Zona de Fresnel (F1)",
      showlegend: false,
      mode: "lines",
      line: { color: color, width: 1 },
      fill: "tonexty",
      fillcolor: hexARgba(color, 0.22),
      hoverinfo: "skip",
    };

    const nombreCriterio = `Criterio de despeje (${criterioDespejePct}% de F1)`;
    const trazaCritSuperior = {
      x: distKm,
      y: critSuperior,
      name: nombreCriterio,
      mode: "lines",
      line: { color: COLOR_CRITERIO, width: 1.5, dash: "dash" },
      hoverinfo: "skip",
    };
    const trazaCritInferior = {
      x: distKm,
      y: critInferior,
      name: nombreCriterio,
      showlegend: false,
      mode: "lines",
      line: { color: COLOR_CRITERIO, width: 1.5, dash: "dash" },
      hoverinfo: "skip",
    };

    const trazaLos = {
      x: distKm,
      y: los,
      name: "Línea de vista",
      mode: "lines",
      line: { color: COLOR_LOS, width: 2 },
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
      line: { color: COLOR_LOS, width: 5 },
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
      trazaLos,
      trazaTorres,
    ];

    if (critica) {
      trazas.push({
        x: [critica.distancia_acumulada_m / 1000],
        y: [critica.altura_linea_vista_m - critica.holgura_m],
        name: "Punto crítico",
        mode: "markers",
        marker: { size: 12, color: color, symbol: "diamond", line: { color: COLOR_LOS, width: 1.5 } },
        hovertemplate: `Punto crítico: %{x:.2f} km<br>Despeje: ${critica.despeje_pct.toFixed(1)}%<extra></extra>`,
      });
    }

    return { trazas, critica, color, minY, maxY };
  }

  function construirAnotaciones(resultado, critica, color, minY, maxY) {
    const anotaciones = [];
    if (critica) {
      const yCritico = critica.altura_linea_vista_m - critica.holgura_m;
      // Si el punto crítico cae en la mitad superior del rango visible (un
      // pico alto, típico del caso obstruido), la anotación apunta hacia
      // abajo en vez de hacia arriba, para no salirse del área del gráfico.
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
        bgcolor: "rgba(252,252,251,0.92)",
        bordercolor: color,
        borderwidth: 1.5,
        font: { color: "#0b0b0b", size: 12 },
      });
    }

    return anotaciones;
  }

  const TITULO_BASE = "Perfil del enlace y primera zona de Fresnel";

  /** Subtítulo con el texto de exageración vertical, como segunda línea del
   * título. Vive en el margen superior del gráfico (no en el área de
   * trazado) a propósito: el punto crítico puede caer cerca de un pico de
   * terreno alto (caso obstruido) y una anotación flotante en la esquina
   * superior chocaría visualmente con él. */
  function tituloConExageracion(texto) {
    return `${TITULO_BASE}<br><span style="font-size:11px;color:${COLOR_TEXTO_SECUNDARIO}">${texto}</span>`;
  }

  /**
   * El eje X está en kilómetros y el Y en metros: cualquier gráfico de
   * perfil de terreno con estos dos ejes independientes exagera
   * visualmente el relieve (es la convención estándar en software de
   * radioenlaces, porque de otro modo el relieve real sería
   * imperceptible). Esta función calcula ese factor de exageración a
   * partir del tamaño real renderizado del área de trazado y lo escribe
   * en el propio gráfico, para que nunca quede implícito.
   */
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

    const texto =
      `Escala vertical exagerada ≈${exageracion.toFixed(0)}× respecto a la horizontal ` +
      "(eje Y en metros, eje X en kilómetros): el relieve NO está a proporción real.";

    const tituloNuevo = tituloConExageracion(texto);
    if (contenedor.layout.title && contenedor.layout.title.text === tituloNuevo) return; // evita relayout redundante
    Plotly.relayout(contenedor, { "title.text": tituloNuevo });
  }

  /**
   * Dibuja el gráfico de perfil + zona de Fresnel en el contenedor dado.
   *
   * @param {object} resultado - la respuesta de POST /api/analizar.
   * @param {number} criterioDespejePct - el criterio de despeje usado en
   *   esa solicitud (no viaja en la respuesta; lo conoce quien hizo la
   *   petición).
   * @param {string} contenedorId - id del elemento donde graficar.
   */
  function renderPerfilFresnel(resultado, criterioDespejePct, contenedorId) {
    const { trazas, critica, color, minY, maxY } = construirTrazas(resultado, criterioDespejePct);
    const anotaciones = construirAnotaciones(resultado, critica, color, minY, maxY);
    const distTotalKm = resultado.distancia_total_m / 1000;

    const layout = {
      title: { text: tituloConExageracion("calculando escala vertical…"), font: { size: 16 } },
      xaxis: {
        title: "Distancia desde A (km)",
        range: [-distTotalKm * 0.02, distTotalKm * 1.02],
        zeroline: false,
        gridcolor: COLOR_EJES,
      },
      yaxis: {
        title: "Elevación (msnm)",
        zeroline: false,
        gridcolor: COLOR_EJES,
      },
      hovermode: "x unified",
      hoverlabel: { bgcolor: "#fcfcfb", font: { color: "#0b0b0b" } },
      legend: { orientation: "h", y: -0.22 },
      margin: { t: 70, r: 20, b: 90, l: 60 },
      annotations: anotaciones,
      paper_bgcolor: "#fcfcfb",
      plot_bgcolor: "#fcfcfb",
    };

    const contenedor = document.getElementById(contenedorId);
    Plotly.newPlot(contenedor, trazas, layout, { responsive: true, displaylogo: false }).then(
      () => actualizarExageracionVertical(contenedor)
    );

    if (!contenedor.__exageracionWireada) {
      contenedor.__exageracionWireada = true;
      window.addEventListener("resize", () => actualizarExageracionVertical(contenedor));
      contenedor.on("plotly_relayout", () => actualizarExageracionVertical(contenedor));
    }
  }

  window.renderPerfilFresnel = renderPerfilFresnel;

  // --- Arnés mínimo para probar con los tres casos demo ---

  function mostrarVeredicto(veredicto) {
    const el = document.getElementById("veredicto");
    if (!el) return;
    el.textContent = veredicto.mensaje;
    el.style.borderColor = ESTADO_COLOR[veredicto.estado] || "#898781";
  }

  function mostrarError(mensaje) {
    const el = document.getElementById("veredicto");
    if (!el) return;
    el.textContent = `Error: ${mensaje}`;
    el.style.borderColor = ESTADO_COLOR.LOS_BLOQUEADA;
  }

  async function cargarDemo(id) {
    const resDemo = await fetch(`/api/demos/${id}`);
    if (!resDemo.ok) {
      throw new Error(`no se pudo cargar el caso demo '${id}' (HTTP ${resDemo.status})`);
    }
    const demo = await resDemo.json();

    const resAnalisis = await fetch("/api/analizar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(demo.parametros),
    });
    if (!resAnalisis.ok) {
      const cuerpo = await resAnalisis.json().catch(() => ({}));
      throw new Error(cuerpo.detail || `error ${resAnalisis.status} al analizar`);
    }
    const resultado = await resAnalisis.json();

    renderPerfilFresnel(resultado, demo.parametros.criterio_despeje_pct, "grafico");
    mostrarVeredicto(resultado.veredicto);
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-demo]").forEach((boton) => {
      boton.addEventListener("click", () => {
        const id = boton.getAttribute("data-demo");
        cargarDemo(id).catch((err) => mostrarError(err.message));
      });
    });

    cargarDemo("despejado").catch((err) => mostrarError(err.message));
  });
})();
