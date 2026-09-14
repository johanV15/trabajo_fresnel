/**
 * Espejo en JavaScript de core/fresnel.py — geometría de la primera zona de
 * Fresnel, curvatura terrestre y veredicto de despeje.
 *
 * DUPLICACIÓN INTENCIONAL (ver Contexto/CLAUDE.md, decisión 1-2 y el
 * docstring de módulo en core/fresnel.py): mover el slider de frecuencia o
 * los campos de altura de torre no puede depender de un round-trip al
 * backend — es el control que más se manipula y el que el evaluador mira
 * con más atención. La elevación del terreno (lo único que sí requiere
 * red) se pide una sola vez a /api/analizar y se cachea en el cliente;
 * este archivo recalcula la geometría de Fresnel sobre ese perfil ya
 * conocido, en el navegador, en cada movimiento.
 *
 * Si se modifica una fórmula aquí, hay que reflejar el mismo cambio en
 * core/fresnel.py, y viceversa.
 */

(function (global) {
  "use strict";

  const VELOCIDAD_LUZ_M_S = 299792458.0;
  const K_ESTANDAR = 4.0 / 3.0;
  const CRITERIO_DESPEJE_ESTANDAR_PCT = 60.0;
  const RADIO_MINIMO_SIGNIFICATIVO_M = 1e-6;

  /** Radio de la primera zona de Fresnel en un punto del trayecto, en metros.
   * r1 = sqrt(lambda * d1 * d2 / (d1 + d2)) — ver core/fresnel.py::radio_fresnel
   * para la explicación física completa. */
  function radioFresnel(d1M, d2M, frecuenciaHz) {
    const distanciaTotalM = d1M + d2M;
    if (distanciaTotalM <= 0) return 0.0;
    const longitudOndaM = VELOCIDAD_LUZ_M_S / frecuenciaHz;
    return Math.sqrt((longitudOndaM * d1M * d2M) / distanciaTotalM);
  }

  /** Abultamiento aparente del terreno por curvatura terrestre, en metros.
   * h_curv = d1_km * d2_km / (12.75 * k) — ver
   * core/fresnel.py::abultamiento_curvatura. */
  function abultamientoCurvatura(d1M, d2M, k) {
    const d1Km = d1M / 1000.0;
    const d2Km = d2M / 1000.0;
    return (d1Km * d2Km) / (12.75 * k);
  }

  function formatoEs(valor) {
    return valor.toFixed(1).replace(".", ",");
  }

  function construirMensaje(estado, distanciaCriticaKm, despejePctCritico, criterioDespejePct) {
    const dist = formatoEs(distanciaCriticaKm);
    const pct = formatoEs(despejePctCritico);
    if (estado === "VIABLE") {
      return `ENLACE VIABLE — despeje mínimo ${pct}% de F1 a ${dist} km del punto A`;
    }
    if (estado === "INVASION_FRESNEL") {
      const criterio = formatoEs(criterioDespejePct);
      return (
        `OBSTRUCCIÓN — el terreno invade la zona de Fresnel a ${dist} km ` +
        `del punto A; despeje ${pct}% (mínimo requerido ${criterio}%)`
      );
    }
    return `LÍNEA DE VISTA BLOQUEADA — el terreno corta la línea directa a ${dist} km del punto A`;
  }

  /**
   * Analiza un enlace completo: para cada muestra del perfil (que ya trae
   * su elevación real, conocida de antemano) calcula radio F1, abultamiento,
   * altura de línea de vista, holgura y % de despeje; determina el
   * veredicto por la muestra de menor despeje.
   *
   * @param {Array<{latitud:number, longitud:number, distancia_acumulada_m:number, elevacion_msnm:number}>} perfil
   * @param {number} elevacionMsnmA
   * @param {number} alturaTorreAM
   * @param {number} elevacionMsnmB
   * @param {number} alturaTorreBM
   * @param {number} frecuenciaHz
   * @param {number} [k]
   * @param {number} [criterioDespejePct]
   */
  function analizarEnlace(
    perfil,
    elevacionMsnmA,
    alturaTorreAM,
    elevacionMsnmB,
    alturaTorreBM,
    frecuenciaHz,
    k,
    criterioDespejePct
  ) {
    k = k === undefined ? K_ESTANDAR : k;
    criterioDespejePct =
      criterioDespejePct === undefined ? CRITERIO_DESPEJE_ESTANDAR_PCT : criterioDespejePct;

    if (perfil.length < 2) {
      throw new Error("el perfil necesita al menos 2 muestras (A y B)");
    }

    const distanciaTotalM = perfil[perfil.length - 1].distancia_acumulada_m;
    const alturaEfectivaAM = elevacionMsnmA + alturaTorreAM;
    const alturaEfectivaBM = elevacionMsnmB + alturaTorreBM;
    const longitudOndaM = VELOCIDAD_LUZ_M_S / frecuenciaHz;
    const radioF1MaximoM = radioFresnel(distanciaTotalM / 2, distanciaTotalM / 2, frecuenciaHz);

    const muestras = perfil.map((punto) => {
      const d1M = punto.distancia_acumulada_m;
      const d2M = distanciaTotalM - d1M;

      const radioF1M = radioFresnel(d1M, d2M, frecuenciaHz);
      const abultamientoM = abultamientoCurvatura(d1M, d2M, k);

      const fraccion = distanciaTotalM > 0 ? d1M / distanciaTotalM : 0.0;
      const alturaLineaVistaM = alturaEfectivaAM + fraccion * (alturaEfectivaBM - alturaEfectivaAM);

      const terrenoEfectivoM = punto.elevacion_msnm + abultamientoM;
      const holguraM = alturaLineaVistaM - terrenoEfectivoM;

      const despejePct =
        radioF1M > RADIO_MINIMO_SIGNIFICATIVO_M ? (holguraM / radioF1M) * 100.0 : null;

      return {
        latitud: punto.latitud,
        longitud: punto.longitud,
        distancia_acumulada_m: d1M,
        radio_f1_m: radioF1M,
        abultamiento_m: abultamientoM,
        altura_linea_vista_m: alturaLineaVistaM,
        holgura_m: holguraM,
        despeje_pct: despejePct,
      };
    });

    let critica = null;
    for (const m of muestras) {
      if (m.despeje_pct === null) continue;
      if (critica === null || m.despeje_pct < critica.despeje_pct) critica = m;
    }
    if (critica === null) {
      throw new Error(
        "todas las muestras del perfil caen en los extremos del enlace (radio F1 = 0)"
      );
    }

    let estado;
    if (critica.despeje_pct < 0) estado = "LOS_BLOQUEADA";
    else if (critica.despeje_pct < criterioDespejePct) estado = "INVASION_FRESNEL";
    else estado = "VIABLE";

    const mensaje = construirMensaje(
      estado,
      critica.distancia_acumulada_m / 1000,
      critica.despeje_pct,
      criterioDespejePct
    );

    return {
      distancia_total_m: distanciaTotalM,
      longitud_onda_m: longitudOndaM,
      radio_f1_maximo_m: radioF1MaximoM,
      elevacion_msnm_a: elevacionMsnmA,
      elevacion_msnm_b: elevacionMsnmB,
      altura_efectiva_a_m: alturaEfectivaAM,
      altura_efectiva_b_m: alturaEfectivaBM,
      muestras: muestras,
      veredicto: {
        estado: estado,
        distancia_punto_critico_m: critica.distancia_acumulada_m,
        latitud_critica: critica.latitud,
        longitud_critica: critica.longitud,
        despeje_pct_critico: critica.despeje_pct,
        mensaje: mensaje,
      },
    };
  }

  global.FresnelCore = {
    VELOCIDAD_LUZ_M_S: VELOCIDAD_LUZ_M_S,
    K_ESTANDAR: K_ESTANDAR,
    CRITERIO_DESPEJE_ESTANDAR_PCT: CRITERIO_DESPEJE_ESTANDAR_PCT,
    radioFresnel: radioFresnel,
    abultamientoCurvatura: abultamientoCurvatura,
    analizarEnlace: analizarEnlace,
  };
})(window);
