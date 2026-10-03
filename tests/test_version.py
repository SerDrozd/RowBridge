from __future__ import annotations

import tomllib
from pathlib import Path

from rowbridge import __version__

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_runtime_version_matches_project_version() -> None:
    pyproject = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert __version__ == pyproject["project"]["version"]
