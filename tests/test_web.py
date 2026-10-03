from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from rowbridge.config import Settings
from rowbridge.web import create_app

A_CSV = (
    b"invoice_ref,customer,amount,date\n"
    b"INV-1,Acme Ltd,10.00,2026-10-01\n"
    b"INV-2,Blue Finch GmbH,20.00,2026-10-02\n"
)
B_CSV = (
    b"reference,payer,total,paid_at\n"
    b"INV1,ACME Limited,10.01,2026-10-02\n"
    b"INV-9,Blue Finch GmbH,20.00,2026-10-02\n"
)


def make_client(tmp_path: Path) -> TestClient:
    app = create_app(Settings(data_dir=tmp_path / "data"))
    return TestClient(app)


def create_sample_run(client: TestClient) -> str:
    prepared = client.post(
        "/prepare",
        files={
            "file_a": ("orders.csv", A_CSV, "text/csv"),
            "file_b": ("payments.csv", B_CSV, "text/csv"),
        },
    )
    assert prepared.status_code == 200
    assert "Secondary text field" in prepared.text
    stage_match = re.search(r'data-stage-id="([a-f0-9]+)"', prepared.text)
    assert stage_match is not None

    created = client.post(
        "/runs",
        data={
            "stage_id": stage_match.group(1),
            "primary_a": "invoice_ref",
            "primary_b": "reference",
            "secondary_a": "customer",
            "secondary_b": "payer",
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
    return created.headers["location"]


def review_match_id(html: str) -> int:
    match = re.search(r'/matches/(\d+)/accept', html)
    assert match is not None
    return int(match.group(1))


def test_vertical_slice_upload_map_persist_render_and_export(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    location = create_sample_run(client)

    results = client.get(location)
    assert results.status_code == 200
    assert "Reconciliation results" in results.text
    assert "INV-1" in results.text
    assert "INV1" in results.text
    assert "Blue Finch GmbH" in results.text
    assert "Accept match" in results.text
    assert "Link unmatched rows manually" in results.text

    fresh_client = make_client(tmp_path)
    persisted = fresh_client.get(location)
    assert persisted.status_code == 200
    assert "INV-1" in persisted.text

    export = fresh_client.get(f"{location}/export.csv")
    assert export.status_code == 200
    assert "status,score,side_a_row" in export.text
    assert "INV-1,INV1" in export.text


def test_accept_review_persists_confirmation_and_audit_event(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    location = create_sample_run(client)
    results = client.get(location)
    match_id = review_match_id(results.text)

    accepted = client.post(
        f"{location}/matches/{match_id}/accept",
        follow_redirects=False,
    )
    assert accepted.status_code == 303

    fresh_client = make_client(tmp_path)
    persisted = fresh_client.get(location)
    assert persisted.status_code == 200
    assert "confirmed" in persisted.text
    assert "Accepted proposed match." in persisted.text
    assert "accept" in persisted.text
    assert "Needs review</span><strong>0" in persisted.text
    assert "Human matched</span><strong>1" in persisted.text

    repeated = fresh_client.post(
        f"{location}/matches/{match_id}/accept",
        follow_redirects=False,
    )
    assert repeated.status_code == 409


def test_reject_review_then_manually_link_unmatched_rows(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    location = create_sample_run(client)
    results = client.get(location)
    match_id = review_match_id(results.text)

    rejected = client.post(
        f"{location}/matches/{match_id}/reject",
        follow_redirects=False,
    )
    assert rejected.status_code == 303

    after_reject = client.get(location)
    assert "Rejected proposed match" in after_reject.text
    assert "Needs review</span><strong>0" in after_reject.text
    assert "Unmatched rows</span><strong>2" in after_reject.text

    a_option = re.search(r'<option value="(\d+)">row 3 · INV-2</option>', after_reject.text)
    b_option = re.search(r'<option value="(\d+)">row 3 · INV-9</option>', after_reject.text)
    assert a_option is not None
    assert b_option is not None

    linked = client.post(
        f"{location}/manual-links",
        data={
            "a_match_id": a_option.group(1),
            "b_match_id": b_option.group(1),
        },
        follow_redirects=False,
    )
    assert linked.status_code == 303

    fresh_client = make_client(tmp_path)
    persisted = fresh_client.get(location)
    assert "manual matched" in persisted.text
    assert "Linked two previously unmatched rows manually." in persisted.text
    assert "Human matched</span><strong>1" in persisted.text
    assert "Unmatched rows</span><strong>0" in persisted.text

    export = fresh_client.get(f"{location}/export.csv")
    assert "manual_matched,,3,3,INV-2,INV-9" in export.text


def test_result_filters_reject_unknown_view(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    location = create_sample_run(client)

    review = client.get(f"{location}?view=review")
    assert review.status_code == 200
    assert "INV-2" in review.text
    assert "INV-1" not in review.text

    invalid = client.get(f"{location}?view=wat")
    assert invalid.status_code == 400


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
