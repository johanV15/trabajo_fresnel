"""Verificación manual de la integración real con Open-Topo-Data.

No es un test automatizado (los tests usan red simulada, ver
tests/test_elevation.py); este script hace una llamada de red real para
comprobar la integración a mano.

Uso:
    python scripts/verificar_elevacion.py

Consulta la elevación del centro de Bogotá (Plaza de Bolívar, elevación
real conocida ≈ 2 600 msnm) dos veces seguidas: la primera debe ir a la
red, la segunda debe salir de la caché en disco sin llamar a la API (y
por lo tanto ser mucho más rápida).
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.elevation import CacheElevacionEnDisco, ErrorElevacion, OpenTopoData, PuntoConsulta

RUTA_CACHE = Path(__file__).resolve().parent.parent / "cache" / "elevaciones.json"

# Plaza de Bolívar, Bogotá. Elevación real conocida: ~2 600 msnm.
PUNTO_BOGOTA = PuntoConsulta(latitud=4.5981, longitud=-74.0761)


def main() -> None:
    fuente = CacheElevacionEnDisco(OpenTopoData(), RUTA_CACHE)

    print(f"Archivo de caché: {RUTA_CACHE}")
    print("Referencia conocida: centro de Bogotá ≈ 2600 msnm\n")

    try:
        print("Primera consulta (puede ir a la red)...")
        inicio = time.monotonic()
        elevacion_1 = fuente.consultar([PUNTO_BOGOTA])[0]
        duracion_1 = time.monotonic() - inicio
        print(f"  Elevación: {elevacion_1:.1f} msnm  (tardó {duracion_1:.2f} s)")

        print("Segunda consulta (debería salir de la caché en disco)...")
        inicio = time.monotonic()
        elevacion_2 = fuente.consultar([PUNTO_BOGOTA])[0]
        duracion_2 = time.monotonic() - inicio
        print(f"  Elevación: {elevacion_2:.1f} msnm  (tardó {duracion_2:.4f} s)")
    except ErrorElevacion as exc:
        print(f"\nERROR consultando Open-Topo-Data: {exc}")
        sys.exit(1)

    assert elevacion_1 == elevacion_2, "la elevación cambió entre consultas"

    if duracion_2 < duracion_1 / 5:
        print("\nOK: la segunda consulta fue mucho más rápida -> vino de la caché.")
    else:
        print(
            "\nADVERTENCIA: la segunda consulta no fue notablemente más rápida; "
            "revisar si realmente vino de la caché (por ejemplo, si la primera "
            "corrida ya estaba cacheada de antes)."
        )

    if 2000 <= elevacion_1 <= 3200:
        print("OK: la elevación está en el rango esperado para Bogotá (2000-3200 msnm).")
    else:
        print(
            f"ADVERTENCIA: {elevacion_1:.1f} msnm está fuera del rango esperado "
            "para Bogotá; revisar el dataset o las coordenadas."
        )


if __name__ == "__main__":
    main()
