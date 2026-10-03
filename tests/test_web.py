from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from rowbridge.config import Settings
from rowbridge.web import create_app

A_CSV = b"invoice_ref,amount,date\nINV-1,10.00,2026-10-01\nINV-2,20.00,2026-10-02\n"
B_CSV = b"reference,total,paid_at\nINV1,10.01,2026-10-02\nOTHER,99.00,2026-10-02\n"


def make_client(tmp_path: Path) -> TestClient:
    app = create_app(Settings(data_dir=tmp_path / "data"))
    return TestClient(app)


def test_vertical_slice_upload_map_persist_render_and_export(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    prepared = client.post(
        "/prepare",
        files={
            "file_a": ("orders.csv", A_CSV, "text/csv"),
            "file_b": ("payments.csv", B_CSV, "text/csv"),
        },
    )
    assert prepared.status_code == 200
    stage_match = re.search(r'data-stage-id="([a-f0-9]+)"', prepared.text)
    assert stage_match is not None

    created = client.post(
        "/runs",
        data={
            "stage_id": stage_match.group(1),
            "primary_a": "invoice_ref",
            "primary_b": "reference",
            "amount_a": "amount",
            "amount_b": "total",
            "date_a": "date",
            "date_b": "paid_at",
            "amount_tolerance": "0.05",
            "date_window_days": "2",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    location = created.headers["location"]

    results = client.get(location)
    assert results.status_code == 200
    assert "Reconciliation results" in results.text
    assert "INV-1" in results.text
    assert "INV1" in results.text

    # A fresh app instance must be able to read the same persisted run.
    fresh_client = make_client(tmp_path)
    persisted = fresh_client.get(location)
    assert persisted.status_code == 200
    assert "INV-1" in persisted.text

    export = fresh_client.get(f"{location}/export.csv")
    assert export.status_code == 200
    assert "status,score,side_a_row" in export.text
    assert "INV-1,INV1" in export.text


def test_prepare_rejects_non_csv_files(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    response = client.post(
        "/prepare",
        files={
            "file_a": ("a.txt", A_CSV, "text/plain"),
            "file_b": ("b.csv", B_CSV, "text/csv"),
        },
    )
    assert response.status_code == 400
