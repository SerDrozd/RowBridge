from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    stage_ttl_seconds: int = 24 * 60 * 60
    results_page_size: int = 100
    manual_link_select_limit: int = 200

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
