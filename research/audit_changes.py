"""Bloquea cambios en carteras, producción y credenciales desde el PR de laboratorio."""
from __future__ import annotations
import argparse
import subprocess
import sys
from pathlib import PurePosixPath

# Solo archivos esperados de investigación, tests, workflow y los dos parches TLS.
ALLOWED_EXACT = {"exchange_connector.py", "market_data.py", ".github/workflows/research-lab.yml"}
ALLOWED_PREFIXES = ("research/", "tests/test_research")


def permitted(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if normalized.startswith("/") or ".." in PurePosixPath(normalized).parts:
        return False
    return normalized in ALLOWED_EXACT or any(normalized.startswith(p) for p in ALLOWED_PREFIXES)


def audit(paths: list[str]) -> list[str]:
    return [p for p in paths if not permitted(p)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Auditar alcance del PR sin escribir archivos")
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args()
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACDMRTUXB", f"{args.base}...HEAD"],
        check=True, capture_output=True, text=True,
    )
    paths = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    forbidden = audit(paths)
    print(f"Archivos cambiados: {len(paths)}; fuera de alcance: {len(forbidden)}")
    if forbidden:
        print("BLOQUEADO: cambios no autorizados en:", *forbidden, sep="\n - ", file=sys.stderr)
        raise SystemExit(1)
    print("Alcance autorizado. No se tocaron archivos de carteras/producción fuera de la lista.")


if __name__ == "__main__":
    main()
