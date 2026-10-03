from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from rowbridge.models import (
    Evidence,
    FieldMapping,
    MatchDecision,
    MatchSettings,
    MatchStatus,
    ReviewAction,
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
class RunSummary:
    auto_matched: int
    human_matched: int
    review: int
    unmatched: int


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


@dataclass(frozen=True, slots=True)
class StoredReviewEvent:
    id: int
    created_at: str
    action: ReviewAction
    previous_status: str | None
    resulting_status: str
    score: float | None
    detail: str
    a_row_number: int | None
    b_row_number: int | None
    a_payload: RowPayload | None
    b_payload: RowPayload | None


class Repository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
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

                CREATE TABLE IF NOT EXISTS review_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    action TEXT NOT NULL,
                    source_match_id INTEGER,
                    a_row_id INTEGER REFERENCES source_rows(id),
                    b_row_id INTEGER REFERENCES source_rows(id),
                    previous_status TEXT,
                    resulting_status TEXT NOT NULL,
                    score REAL,
                    detail TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_source_rows_run ON source_rows(run_id, side);
                CREATE INDEX IF NOT EXISTS idx_matches_run ON matches(run_id, status);
                CREATE INDEX IF NOT EXISTS idx_review_events_run ON review_events(run_id, id);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_matches_unique_a
                    ON matches(run_id, a_row_id) WHERE a_row_id IS NOT NULL;
                CREATE UNIQUE INDEX IF NOT EXISTS idx_matches_unique_b
                    ON matches(run_id, b_row_id) WHERE b_row_id IS NOT NULL;
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

    def get_summary(self, run_id: str) -> RunSummary:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM matches
                WHERE run_id = ?
                GROUP BY status
                """,
                (run_id,),
            ).fetchall()
        counts = {str(row["status"]): int(row["count"]) for row in rows}
        return RunSummary(
            auto_matched=counts.get(MatchStatus.AUTO_MATCHED.value, 0),
            human_matched=(
                counts.get(MatchStatus.CONFIRMED.value, 0)
                + counts.get(MatchStatus.MANUAL_MATCHED.value, 0)
            ),
            review=counts.get(MatchStatus.REVIEW.value, 0),
            unmatched=counts.get(MatchStatus.UNMATCHED.value, 0),
        )

    @staticmethod
    def _matches_from_rows(rows: list[sqlite3.Row]) -> tuple[StoredMatch, ...]:
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

    @staticmethod
    def _status_clause(statuses: tuple[MatchStatus, ...] | None) -> tuple[str, list[str]]:
        if not statuses:
            return "", []
        placeholders = ", ".join("?" for _ in statuses)
        return f" AND m.status IN ({placeholders})", [status.value for status in statuses]

    def count_matches(
        self,
        run_id: str,
        statuses: tuple[MatchStatus, ...] | None = None,
    ) -> int:
        status_clause, status_values = self._status_clause(statuses)
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) AS count FROM matches m WHERE m.run_id = ?{status_clause}",
                [run_id, *status_values],
            ).fetchone()
        return 0 if row is None else int(row["count"])

    def list_matches(
        self,
        run_id: str,
        statuses: tuple[MatchStatus, ...] | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[StoredMatch, ...]:
        if offset < 0:
            raise ValueError("offset cannot be negative")
        if limit is not None and limit <= 0:
            raise ValueError("limit must be positive")

        status_clause, status_values = self._status_clause(statuses)
        query = f"""
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
            WHERE m.run_id = ?{status_clause}
            ORDER BY
                CASE m.status
                    WHEN 'review' THEN 0
                    WHEN 'confirmed' THEN 1
                    WHEN 'manual_matched' THEN 2
                    WHEN 'auto_matched' THEN 3
                    ELSE 4
                END,
                COALESCE(a.row_number, 1000000000),
                COALESCE(b.row_number, 1000000000)
        """
        params: list[str | int] = [run_id, *status_values]
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend((limit, offset))

        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return self._matches_from_rows(rows)

    def count_unmatched_side(self, run_id: str, side: str) -> int:
        if side not in {"a", "b"}:
            raise ValueError("side must be 'a' or 'b'")
        present = "a_row_id" if side == "a" else "b_row_id"
        absent = "b_row_id" if side == "a" else "a_row_id"
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM matches
                WHERE run_id = ? AND status = ?
                  AND {present} IS NOT NULL AND {absent} IS NULL
                """,
                (run_id, MatchStatus.UNMATCHED.value),
            ).fetchone()
        return 0 if row is None else int(row["count"])

    def list_unmatched_side(
        self,
        run_id: str,
        side: str,
        *,
        limit: int,
    ) -> tuple[StoredMatch, ...]:
        if side not in {"a", "b"}:
            raise ValueError("side must be 'a' or 'b'")
        if limit <= 0:
            raise ValueError("limit must be positive")
        present = "m.a_row_id" if side == "a" else "m.b_row_id"
        absent = "m.b_row_id" if side == "a" else "m.a_row_id"
        query = f"""
            SELECT
                m.id, m.status, m.score, m.evidence_json,
                a.row_number AS a_row_number, a.payload_json AS a_payload_json,
                b.row_number AS b_row_number, b.payload_json AS b_payload_json
            FROM matches m
            LEFT JOIN source_rows a ON a.id = m.a_row_id
            LEFT JOIN source_rows b ON b.id = m.b_row_id
            WHERE m.run_id = ? AND m.status = ?
              AND {present} IS NOT NULL AND {absent} IS NULL
            ORDER BY COALESCE(a.row_number, b.row_number)
            LIMIT ?
        """
        with self._connect() as connection:
            rows = connection.execute(
                query,
                (run_id, MatchStatus.UNMATCHED.value, limit),
            ).fetchall()
        return self._matches_from_rows(rows)

    def get_unmatched_match_by_row(
        self,
        run_id: str,
        side: str,
        row_number: int,
    ) -> StoredMatch | None:
        if side not in {"a", "b"}:
            raise ValueError("side must be 'a' or 'b'")
        if row_number < 2:
            return None
        row_alias = "a" if side == "a" else "b"
        present = "m.a_row_id" if side == "a" else "m.b_row_id"
        absent = "m.b_row_id" if side == "a" else "m.a_row_id"
        query = f"""
            SELECT
                m.id, m.status, m.score, m.evidence_json,
                a.row_number AS a_row_number, a.payload_json AS a_payload_json,
                b.row_number AS b_row_number, b.payload_json AS b_payload_json
            FROM matches m
            LEFT JOIN source_rows a ON a.id = m.a_row_id
            LEFT JOIN source_rows b ON b.id = m.b_row_id
            WHERE m.run_id = ? AND m.status = ?
              AND {present} IS NOT NULL AND {absent} IS NULL
              AND {row_alias}.row_number = ?
            LIMIT 1
        """
        with self._connect() as connection:
            row = connection.execute(
                query,
                (run_id, MatchStatus.UNMATCHED.value, row_number),
            ).fetchone()
        if row is None:
            return None
        return self._matches_from_rows([row])[0]

    def accept_review(self, run_id: str, match_id: int) -> None:
        with self._connect() as connection:
            row = self._get_match_row(connection, run_id, match_id)
            self._require_status(row, MatchStatus.REVIEW)
            connection.execute(
                "UPDATE matches SET status = ? WHERE id = ?",
                (MatchStatus.CONFIRMED.value, match_id),
            )
            self._record_event(
                connection=connection,
                run_id=run_id,
                action=ReviewAction.ACCEPT,
                source_match_id=match_id,
                a_row_id=self._optional_int(row["a_row_id"]),
                b_row_id=self._optional_int(row["b_row_id"]),
                previous_status=MatchStatus.REVIEW.value,
                resulting_status=MatchStatus.CONFIRMED.value,
                score=float(row["score"]),
                detail="Accepted proposed match.",
            )
            self._refresh_run_counts(connection, run_id)

    def reject_review(self, run_id: str, match_id: int) -> None:
        with self._connect() as connection:
            row = self._get_match_row(connection, run_id, match_id)
            self._require_status(row, MatchStatus.REVIEW)
            a_row_id = self._optional_int(row["a_row_id"])
            b_row_id = self._optional_int(row["b_row_id"])
            if a_row_id is None or b_row_id is None:
                raise ValueError("A review pair must contain one row from each side")

            connection.execute("DELETE FROM matches WHERE id = ?", (match_id,))
            self._insert_unmatched(connection, run_id, a_row_id, side="a")
            self._insert_unmatched(connection, run_id, b_row_id, side="b")
            self._record_event(
                connection=connection,
                run_id=run_id,
                action=ReviewAction.REJECT,
                source_match_id=match_id,
                a_row_id=a_row_id,
                b_row_id=b_row_id,
                previous_status=MatchStatus.REVIEW.value,
                resulting_status=MatchStatus.UNMATCHED.value,
                score=float(row["score"]),
                detail="Rejected proposed match; both rows returned to unmatched.",
            )
            self._refresh_run_counts(connection, run_id)

    def create_manual_link(self, run_id: str, a_match_id: int, b_match_id: int) -> None:
        if a_match_id == b_match_id:
            raise ValueError("Select one unmatched row from each side")

        with self._connect() as connection:
            a_match = self._get_match_row(connection, run_id, a_match_id)
            b_match = self._get_match_row(connection, run_id, b_match_id)
            self._require_status(a_match, MatchStatus.UNMATCHED)
            self._require_status(b_match, MatchStatus.UNMATCHED)

            a_row_id = self._optional_int(a_match["a_row_id"])
            b_row_id = self._optional_int(b_match["b_row_id"])
            if a_row_id is None or a_match["b_row_id"] is not None:
                raise ValueError("The Side A selection is not an unmatched Side A row")
            if b_row_id is None or b_match["a_row_id"] is not None:
                raise ValueError("The Side B selection is not an unmatched Side B row")

            connection.execute(
                "DELETE FROM matches WHERE id IN (?, ?)",
                (a_match_id, b_match_id),
            )
            evidence = json.dumps(
                [
                    asdict(
                        Evidence(
                            field="review",
                            detail="Linked manually; algorithmic score was not used.",
                            score=0.0,
                        )
                    )
                ]
            )
            cursor = connection.execute(
                """
                INSERT INTO matches (run_id, a_row_id, b_row_id, score, status, evidence_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    a_row_id,
                    b_row_id,
                    0.0,
                    MatchStatus.MANUAL_MATCHED.value,
                    evidence,
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite did not return a match id")
            self._record_event(
                connection=connection,
                run_id=run_id,
                action=ReviewAction.MANUAL_LINK,
                source_match_id=cursor.lastrowid,
                a_row_id=a_row_id,
                b_row_id=b_row_id,
                previous_status=MatchStatus.UNMATCHED.value,
                resulting_status=MatchStatus.MANUAL_MATCHED.value,
                score=None,
                detail="Linked two previously unmatched rows manually.",
            )
            self._refresh_run_counts(connection, run_id)

    def list_review_events(
        self,
        run_id: str,
        *,
        limit: int | None = None,
    ) -> tuple[StoredReviewEvent, ...]:
        if limit is not None and limit <= 0:
            raise ValueError("limit must be positive")
        query = """
            SELECT
                e.id,
                e.created_at,
                e.action,
                e.previous_status,
                e.resulting_status,
                e.score,
                e.detail,
                a.row_number AS a_row_number,
                a.payload_json AS a_payload_json,
                b.row_number AS b_row_number,
                b.payload_json AS b_payload_json
            FROM review_events e
            LEFT JOIN source_rows a ON a.id = e.a_row_id
            LEFT JOIN source_rows b ON b.id = e.b_row_id
            WHERE e.run_id = ?
            ORDER BY e.id DESC
        """
        params: list[str | int] = [run_id]
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()

        events: list[StoredReviewEvent] = []
        for row in rows:
            events.append(
                StoredReviewEvent(
                    id=int(row["id"]),
                    created_at=str(row["created_at"]),
                    action=ReviewAction(str(row["action"])),
                    previous_status=(
                        str(row["previous_status"])
                        if row["previous_status"] is not None
                        else None
                    ),
                    resulting_status=str(row["resulting_status"]),
                    score=float(row["score"]) if row["score"] is not None else None,
                    detail=str(row["detail"]),
                    a_row_number=(
                        int(row["a_row_number"]) if row["a_row_number"] is not None else None
                    ),
                    b_row_number=(
                        int(row["b_row_number"]) if row["b_row_number"] is not None else None
                    ),
                    a_payload=(
                        json.loads(str(row["a_payload_json"]))
                        if row["a_payload_json"] is not None
                        else None
                    ),
                    b_payload=(
                        json.loads(str(row["b_payload_json"]))
                        if row["b_payload_json"] is not None
                        else None
                    ),
                )
            )
        return tuple(events)

    @staticmethod
    def _get_match_row(
        connection: sqlite3.Connection,
        run_id: str,
        match_id: int,
    ) -> sqlite3.Row:
        row: sqlite3.Row | None = connection.execute(
            "SELECT * FROM matches WHERE id = ? AND run_id = ?",
            (match_id, run_id),
        ).fetchone()
        if row is None:
            raise ValueError("Match not found for this run")
        return row

    @staticmethod
    def _require_status(row: sqlite3.Row, expected: MatchStatus) -> None:
        if str(row["status"]) != expected.value:
            raise ValueError(f"Match is not in {expected.value} state")

    @staticmethod
    def _optional_int(value: object) -> int | None:
        if value is None:
            return None
        if not isinstance(value, (int, float, str, bytes, bytearray)):
            value_type = type(value).__name__
            raise TypeError(f"Expected an SQLite integer-compatible value, got {value_type}")
        return int(value)

    @staticmethod
    def _insert_unmatched(
        connection: sqlite3.Connection,
        run_id: str,
        row_id: int,
        side: str,
    ) -> None:
        values: tuple[str, int | None, int | None, float, str, str]
        if side == "a":
            values = (run_id, row_id, None, 0.0, MatchStatus.UNMATCHED.value, "[]")
        else:
            values = (run_id, None, row_id, 0.0, MatchStatus.UNMATCHED.value, "[]")
        connection.execute(
            """
            INSERT INTO matches (run_id, a_row_id, b_row_id, score, status, evidence_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            values,
        )

    @staticmethod
    def _refresh_run_counts(connection: sqlite3.Connection, run_id: str) -> None:
        rows = connection.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM matches
            WHERE run_id = ?
            GROUP BY status
            """,
            (run_id,),
        ).fetchall()
        counts = {str(row["status"]): int(row["count"]) for row in rows}
        connection.execute(
            """
            UPDATE runs
            SET auto_count = ?, review_count = ?, unmatched_count = ?
            WHERE id = ?
            """,
            (
                counts.get(MatchStatus.AUTO_MATCHED.value, 0),
                counts.get(MatchStatus.REVIEW.value, 0),
                counts.get(MatchStatus.UNMATCHED.value, 0),
                run_id,
            ),
        )

    @staticmethod
    def _record_event(
        connection: sqlite3.Connection,
        run_id: str,
        action: ReviewAction,
        source_match_id: int,
        a_row_id: int | None,
        b_row_id: int | None,
        previous_status: str | None,
        resulting_status: str,
        score: float | None,
        detail: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO review_events (
                run_id, created_at, action, source_match_id, a_row_id, b_row_id,
                previous_status, resulting_status, score, detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                datetime.now(UTC).isoformat(timespec="seconds"),
                action.value,
                source_match_id,
                a_row_id,
                b_row_id,
                previous_status,
                resulting_status,
                score,
                detail,
            ),
        )
