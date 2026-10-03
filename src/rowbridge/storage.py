from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

from rowbridge.models import (
    Evidence,
    FieldMapping,
    MatchDecision,
    MatchSettings,
    MatchStatus,
    RowPayload,
)


@dataclass(frozen=True, slots=True)
class StoredRun:
    id: str
    created_at: str
    filename_a: str
    filename_b: str
    mapping: FieldMapping
    settings: MatchSettings
    total_a: int
    total_b: int
    auto_count: int
    review_count: int
    unmatched_count: int


@dataclass(frozen=True, slots=True)
class StoredMatch:
    id: int
    status: MatchStatus
    score: float
    a_row_number: int | None
    b_row_number: int | None
    a_payload: RowPayload | None
    b_payload: RowPayload | None
    evidence: tuple[Evidence, ...]


class Repository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    filename_a TEXT NOT NULL,
                    filename_b TEXT NOT NULL,
                    mapping_json TEXT NOT NULL,
                    settings_json TEXT NOT NULL,
                    total_a INTEGER NOT NULL,
                    total_b INTEGER NOT NULL,
                    auto_count INTEGER NOT NULL,
                    review_count INTEGER NOT NULL,
                    unmatched_count INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS source_rows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    side TEXT NOT NULL CHECK(side IN ('a', 'b')),
                    row_number INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(run_id, side, row_number)
                );

                CREATE TABLE IF NOT EXISTS matches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    a_row_id INTEGER REFERENCES source_rows(id),
                    b_row_id INTEGER REFERENCES source_rows(id),
                    score REAL NOT NULL,
                    status TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    CHECK(a_row_id IS NOT NULL OR b_row_id IS NOT NULL)
                );

                CREATE INDEX IF NOT EXISTS idx_source_rows_run ON source_rows(run_id, side);
                CREATE INDEX IF NOT EXISTS idx_matches_run ON matches(run_id, status);
                """
            )

    def save_run(
        self,
        run_id: str,
        created_at: str,
        filename_a: str,
        filename_b: str,
        mapping: FieldMapping,
        settings: MatchSettings,
        rows_a: tuple[RowPayload, ...],
        rows_b: tuple[RowPayload, ...],
        decisions: tuple[MatchDecision, ...],
    ) -> None:
        auto_count = sum(decision.status == MatchStatus.AUTO_MATCHED for decision in decisions)
        review_count = sum(decision.status == MatchStatus.REVIEW for decision in decisions)
        unmatched_count = sum(decision.status == MatchStatus.UNMATCHED for decision in decisions)

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runs (
                    id, created_at, filename_a, filename_b, mapping_json, settings_json,
                    total_a, total_b, auto_count, review_count, unmatched_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    created_at,
                    filename_a,
                    filename_b,
                    json.dumps(asdict(mapping)),
                    json.dumps(asdict(settings)),
                    len(rows_a),
                    len(rows_b),
                    auto_count,
                    review_count,
                    unmatched_count,
                ),
            )

            row_ids: dict[tuple[str, int], int] = {}
            for side, rows in (("a", rows_a), ("b", rows_b)):
                for index, payload in enumerate(rows):
                    cursor = connection.execute(
                        """
                        INSERT INTO source_rows (run_id, side, row_number, payload_json)
                        VALUES (?, ?, ?, ?)
                        """,
                        (run_id, side, index + 2, json.dumps(payload, ensure_ascii=False)),
                    )
                    if cursor.lastrowid is None:
                        raise RuntimeError("SQLite did not return a row id")
                    row_ids[(side, index)] = cursor.lastrowid

            for decision in decisions:
                evidence_json = json.dumps(
                    [asdict(evidence) for evidence in decision.evidence],
                    ensure_ascii=False,
                )
                connection.execute(
                    """
                    INSERT INTO matches (run_id, a_row_id, b_row_id, score, status, evidence_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        (
                            row_ids.get(("a", decision.a_index))
                            if decision.a_index is not None
                            else None
                        ),
                        (
                            row_ids.get(("b", decision.b_index))
                            if decision.b_index is not None
                            else None
                        ),
                        decision.score,
                        decision.status.value,
                        evidence_json,
                    ),
                )

    def get_run(self, run_id: str) -> StoredRun | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        mapping_data = json.loads(str(row["mapping_json"]))
        settings_data = json.loads(str(row["settings_json"]))
        return StoredRun(
            id=str(row["id"]),
            created_at=str(row["created_at"]),
            filename_a=str(row["filename_a"]),
            filename_b=str(row["filename_b"]),
            mapping=FieldMapping(**mapping_data),
            settings=MatchSettings(**settings_data),
            total_a=int(row["total_a"]),
            total_b=int(row["total_b"]),
            auto_count=int(row["auto_count"]),
            review_count=int(row["review_count"]),
            unmatched_count=int(row["unmatched_count"]),
        )

    def list_matches(self, run_id: str) -> tuple[StoredMatch, ...]:
        query = """
            SELECT
                m.id,
                m.status,
                m.score,
                m.evidence_json,
                a.row_number AS a_row_number,
                a.payload_json AS a_payload_json,
                b.row_number AS b_row_number,
                b.payload_json AS b_payload_json
            FROM matches m
            LEFT JOIN source_rows a ON a.id = m.a_row_id
            LEFT JOIN source_rows b ON b.id = m.b_row_id
            WHERE m.run_id = ?
            ORDER BY
                CASE m.status WHEN 'review' THEN 0 WHEN 'auto_matched' THEN 1 ELSE 2 END,
                COALESCE(a.row_number, 1000000000),
                COALESCE(b.row_number, 1000000000)
        """
        with self._connect() as connection:
            rows = connection.execute(query, (run_id,)).fetchall()

        result: list[StoredMatch] = []
        for row in rows:
            evidence_raw = json.loads(str(row["evidence_json"]))
            evidence = tuple(Evidence(**item) for item in evidence_raw)
            a_payload = (
                json.loads(str(row["a_payload_json"]))
                if row["a_payload_json"] is not None
                else None
            )
            b_payload = (
                json.loads(str(row["b_payload_json"]))
                if row["b_payload_json"] is not None
                else None
            )
            result.append(
                StoredMatch(
                    id=int(row["id"]),
                    status=MatchStatus(str(row["status"])),
                    score=float(row["score"]),
                    a_row_number=(
                        int(row["a_row_number"]) if row["a_row_number"] is not None else None
                    ),
                    b_row_number=(
                        int(row["b_row_number"]) if row["b_row_number"] is not None else None
                    ),
                    a_payload=a_payload,
                    b_payload=b_payload,
                    evidence=evidence,
                )
            )
        return tuple(result)
