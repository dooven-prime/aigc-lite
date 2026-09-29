"""Copy the reproducible React build into the Python package."""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "frontend" / "dist").resolve()
TARGET = (ROOT / "app" / "static").resolve()


def main() -> None:
    if SOURCE.parent != (ROOT / "frontend").resolve() or not SOURCE.is_dir():
        raise RuntimeError("frontend/dist does not exist; run npm build first")
    if TARGET.parent != (ROOT / "app").resolve():
        raise RuntimeError("refusing to replace a static directory outside app")
    if TARGET.exists():
        shutil.rmtree(TARGET)
    shutil.copytree(SOURCE, TARGET)


if __name__ == "__main__":
    main()
