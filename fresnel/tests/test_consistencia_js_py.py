"""Verifica que core/fresnel.py y web/fresnel.js dan el mismo resultado numérico.

Son una duplicación intencional (ver el docstring de core/fresnel.py y
Contexto/CLAUDE.md, decisión 1-2): el slider de frecuencia recalcula en el
navegador para que sea instantáneo, así que la misma fórmula vive en dos
lenguajes. Este test es la manera de detectar que se desincronizaron.

No hay Node disponible en todos los entornos, así que se usa Chrome headless
(si está instalado) como motor de JavaScript vía `--dump-dom`. Si no se
encuentra un Chrome, el test se salta en vez de fallar: no es una
verificación que deba bloquear un entorno sin navegador, pero si Chrome
existe, debe pasar.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core.fresnel import MuestraTerreno, analizar_enlace

_RUTA_FRESNEL_JS = Path(__file__).resolve().parent.parent / "web" / "fresnel.js"

_CANDIDATOS_CHROME = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome",
    "chromium",
    "chromium-browser",
]


def _encontrar_chrome():
    for candidato in _CANDIDATOS_CHROME:
        if Path(candidato).exists():
            return candidato
        encontrado = shutil.which(candidato)
        if encontrado:
            return encontrado
    return None


def _ejecutar_en_js(perfil_js, elevacion_a, altura_a, elevacion_b, altura_b, frecuencia_hz, k, criterio):
    """Corre FresnelCore.analizarEnlace en Chrome headless y devuelve el
    resultado como dict (vía JSON escrito en el DOM)."""
    chrome = _encontrar_chrome()
    if chrome is None:
        pytest.skip("no se encontró Chrome; se salta la verificación de consistencia JS/Python")

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"></head><body>
<pre id="out">pendiente</pre>
<script src="file://{_RUTA_FRESNEL_JS}"></script>
<script>
  const perfil = {json.dumps(perfil_js)};
  const r = FresnelCore.analizarEnlace(
    perfil, {elevacion_a}, {altura_a}, {elevacion_b}, {altura_b}, {frecuencia_hz}, {k}, {criterio}
  );
  document.getElementById("out").textContent = JSON.stringify(r);
</script>
</body></html>"""

    tmp_html = _RUTA_FRESNEL_JS.parent / "_tmp_test_consistencia.html"
    tmp_html.write_text(html, encoding="utf-8")
    try:
        resultado = subprocess.run(
            [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--allow-file-access-from-files",
                "--dump-dom",
                f"file://{tmp_html}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        tmp_html.unlink(missing_ok=True)

    dom = resultado.stdout
    inicio = dom.index('<pre id="out">') + len('<pre id="out">')
    fin = dom.index("</pre>", inicio)
    return json.loads(dom[inicio:fin])


CASOS = [
    # (nombre, perfil elevaciones, alturas, frecuencia_hz, k, criterio)
    (
        "plano_viable",
        [100.0, 100.0, 100.0, 100.0, 100.0],
        30.0, 30.0, 5e9, 4 / 3, 60.0,
    ),
    (
        "pico_invasion",
        [100.0, 105.0, 108.0, 105.0, 100.0],
        10.0, 10.0, 5e9, 4 / 3, 60.0,
    ),
    (
        "pico_bloqueado",
        [100.0, 115.0, 120.0, 115.0, 100.0],
        10.0, 10.0, 2.4e9, 4 / 3, 60.0,
    ),
    (
        "otra_frecuencia_y_k",
        [50.0, 60.0, 90.0, 60.0, 50.0],
        20.0, 25.0, 900e6, 1.2, 50.0,
    ),
]


@pytest.mark.parametrize("nombre,elevaciones,altura_a,altura_b,freq,k,criterio", CASOS)
def test_js_y_python_coinciden(nombre, elevaciones, altura_a, altura_b, freq, k, criterio):
    n = len(elevaciones)
    distancia_total_m = 10_000.0
    paso = distancia_total_m / (n - 1)

    perfil_py = [
        MuestraTerreno(
            latitud=4.0 + i * 0.001,
            longitud=-74.0 + i * 0.001,
            distancia_acumulada_m=i * paso,
            elevacion_msnm=elevaciones[i],
        )
        for i in range(n)
    ]
    perfil_js = [
        {
            "latitud": 4.0 + i * 0.001,
            "longitud": -74.0 + i * 0.001,
            "distancia_acumulada_m": i * paso,
            "elevacion_msnm": elevaciones[i],
        }
        for i in range(n)
    ]

    resultado_py = analizar_enlace(
        perfil_py,
        elevacion_msnm_a=elevaciones[0],
        altura_torre_a_m=altura_a,
        elevacion_msnm_b=elevaciones[-1],
        altura_torre_b_m=altura_b,
        frecuencia_hz=freq,
        k=k,
        criterio_despeje_pct=criterio,
    )
    resultado_js = _ejecutar_en_js(
        perfil_js, elevaciones[0], altura_a, elevaciones[-1], altura_b, freq, k, criterio
    )

    assert resultado_js["veredicto"]["estado"] == resultado_py.veredicto.estado.value
    assert resultado_js["veredicto"]["despeje_pct_critico"] == pytest.approx(
        resultado_py.veredicto.despeje_pct_critico, abs=1e-6
    )
    assert resultado_js["veredicto"]["mensaje"] == resultado_py.veredicto.mensaje
    assert resultado_js["radio_f1_maximo_m"] == pytest.approx(
        resultado_py.radio_f1_maximo_m, abs=1e-9
    )
    for m_py, m_js in zip(resultado_py.muestras, resultado_js["muestras"]):
        assert m_js["radio_f1_m"] == pytest.approx(m_py.radio_f1_m, abs=1e-9)
        assert m_js["holgura_m"] == pytest.approx(m_py.holgura_m, abs=1e-9)
        if m_py.despeje_pct is None:
            assert m_js["despeje_pct"] is None
        else:
            assert m_js["despeje_pct"] == pytest.approx(m_py.despeje_pct, abs=1e-6)
