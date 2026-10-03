from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path

    @property
    def database_path(self) -> Path:
        return self.data_dir / "rowbridge.sqlite3"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @classmethod
    def from_env(cls) -> Settings:
        configured = os.environ.get("ROWBRIDGE_DATA_DIR")
        data_dir = Path(configured).expanduser() if configured else Path.cwd() / ".rowbridge"
        return cls(data_dir=data_dir)

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
