from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from rowbridge.config import Settings
from rowbridge.ingestion import CsvInputError, parse_csv_bytes, read_staged_csv, write_staged_csv
from rowbridge.models import CsvTable, FieldMapping, MatchSettings, MatchStatus, RowPayload
from rowbridge.service import create_reconciliation_run
from rowbridge.storage import Repository, StoredMatch

PACKAGE_DIR = Path(__file__).resolve().parent
MATCHED_STATUSES = {
    MatchStatus.AUTO_MATCHED,
    MatchStatus.CONFIRMED,
    MatchStatus.MANUAL_MATCHED,
}


def _optional_column(value: str) -> str | None:
    stripped = value.strip()
    return stripped or None


def _payload_value(payload: RowPayload | None, column: str) -> str:
    if payload is None:
        return ""
    return payload.get(column, "")


def _display_value(match: StoredMatch, side: str, column: str) -> str:
    payload = match.a_payload if side == "a" else match.b_payload
    return _payload_value(payload, column)


def _filter_matches(matches: tuple[StoredMatch, ...], view: str) -> tuple[StoredMatch, ...]:
    if view == "all":
        return matches
    if view == "review":
        return tuple(match for match in matches if match.status == MatchStatus.REVIEW)
    if view == "matched":
        return tuple(match for match in matches if match.status in MATCHED_STATUSES)
    if view == "unmatched":
        return tuple(match for match in matches if match.status == MatchStatus.UNMATCHED)
    raise ValueError("Unknown result view")


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings.from_env()
    app_settings.ensure_directories()
    repository = Repository(app_settings.database_path)
    repository.initialize()
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")

    app = FastAPI(title="RowBridge", version="0.3.0")
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request=request, name="index.html", context={})

    @app.post("/prepare", response_class=HTMLResponse)
    async def prepare(
        request: Request,
        file_a: Annotated[UploadFile, File()],
        file_b: Annotated[UploadFile, File()],
    ) -> HTMLResponse:
        filename_a = file_a.filename or "side-a.csv"
        filename_b = file_b.filename or "side-b.csv"
        if not filename_a.lower().endswith(".csv") or not filename_b.lower().endswith(".csv"):
            raise HTTPException(status_code=400, detail="This version accepts CSV files only")

        content_a = await file_a.read()
        content_b = await file_b.read()
        try:
            table_a = parse_csv_bytes(content_a, filename_a)
            table_b = parse_csv_bytes(content_b, filename_b)
        except CsvInputError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        stage_id = uuid4().hex
        stage_dir = app_settings.upload_dir / stage_id
        write_staged_csv(stage_dir / "a.csv", content_a)
        write_staged_csv(stage_dir / "b.csv", content_b)
        (stage_dir / "metadata.json").write_text(
            json.dumps({"filename_a": filename_a, "filename_b": filename_b}),
            encoding="utf-8",
        )

        return templates.TemplateResponse(
            request=request,
            name="mapping.html",
            context={
                "stage_id": stage_id,
                "table_a": table_a,
                "table_b": table_b,
            },
        )

    @app.post("/runs")
    def create_run(
        stage_id: str = Form(...),
        primary_a: str = Form(...),
        primary_b: str = Form(...),
        secondary_a: str = Form(""),
        secondary_b: str = Form(""),
        amount_a: str = Form(""),
        amount_b: str = Form(""),
        date_a: str = Form(""),
        date_b: str = Form(""),
        amount_tolerance: float = Form(0.05),
        date_window_days: int = Form(2),
    ) -> RedirectResponse:
        if not stage_id.isalnum():
            raise HTTPException(status_code=400, detail="Invalid staged upload id")
        stage_dir = app_settings.upload_dir / stage_id
        try:
            table_a = read_staged_csv(stage_dir / "a.csv")
            table_b = read_staged_csv(stage_dir / "b.csv")
        except CsvInputError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        metadata_path = stage_dir / "metadata.json"
        if metadata_path.is_file():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            filename_a = str(metadata.get("filename_a", table_a.filename))
            filename_b = str(metadata.get("filename_b", table_b.filename))
            table_a = CsvTable(filename_a, table_a.headers, table_a.rows)
            table_b = CsvTable(filename_b, table_b.headers, table_b.rows)

        if amount_tolerance < 0 or date_window_days < 0:
            raise HTTPException(status_code=400, detail="Tolerances cannot be negative")

        mapping = FieldMapping(
            primary_a=primary_a,
            primary_b=primary_b,
            secondary_a=_optional_column(secondary_a),
            secondary_b=_optional_column(secondary_b),
            amount_a=_optional_column(amount_a),
            amount_b=_optional_column(amount_b),
            date_a=_optional_column(date_a),
            date_b=_optional_column(date_b),
        )
        match_settings = MatchSettings(
            amount_tolerance=amount_tolerance,
            date_window_days=date_window_days,
        )
        try:
            run_id = create_reconciliation_run(
                repository=repository,
                table_a=table_a,
                table_b=table_b,
                mapping=mapping,
                settings=match_settings,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return RedirectResponse(url=f"/runs/{run_id}", status_code=303)

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def results(request: Request, run_id: str, view: str = "all") -> HTMLResponse:
        run = repository.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        all_matches = repository.list_matches(run_id)
        try:
            visible_matches = _filter_matches(all_matches, view)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        unmatched_a = tuple(
            match
            for match in all_matches
            if match.status == MatchStatus.UNMATCHED and match.a_payload is not None
        )
        unmatched_b = tuple(
            match
            for match in all_matches
            if match.status == MatchStatus.UNMATCHED and match.b_payload is not None
        )
        return templates.TemplateResponse(
            request=request,
            name="results.html",
            context={
                "run": run,
                "summary": repository.get_summary(run_id),
                "matches": visible_matches,
                "events": repository.list_review_events(run_id),
                "unmatched_a": unmatched_a,
                "unmatched_b": unmatched_b,
                "active_view": view,
                "display_value": _display_value,
                "payload_value": _payload_value,
            },
        )

    @app.post("/runs/{run_id}/matches/{match_id}/accept")
    def accept_match(run_id: str, match_id: int) -> RedirectResponse:
        if repository.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found")
        try:
            repository.accept_review(run_id, match_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return RedirectResponse(url=f"/runs/{run_id}#review-history", status_code=303)

    @app.post("/runs/{run_id}/matches/{match_id}/reject")
    def reject_match(run_id: str, match_id: int) -> RedirectResponse:
        if repository.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found")
        try:
            repository.reject_review(run_id, match_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return RedirectResponse(url=f"/runs/{run_id}#manual-link", status_code=303)

    @app.post("/runs/{run_id}/manual-links")
    def manual_link(
        run_id: str,
        a_match_id: Annotated[int, Form()],
        b_match_id: Annotated[int, Form()],
    ) -> RedirectResponse:
        if repository.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found")
        try:
            repository.create_manual_link(run_id, a_match_id, b_match_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return RedirectResponse(url=f"/runs/{run_id}#review-history", status_code=303)

    @app.get("/runs/{run_id}/export.csv")
    def export_csv(run_id: str) -> StreamingResponse:
        run = repository.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        matches = repository.list_matches(run_id)

        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(
            [
                "status",
                "score",
                "side_a_row",
                "side_b_row",
                "side_a_primary",
                "side_b_primary",
                "evidence",
            ]
        )
        for match in matches:
            evidence = " | ".join(item.detail for item in match.evidence)
            score = "" if match.status == MatchStatus.MANUAL_MATCHED else f"{match.score:.4f}"
            writer.writerow(
                [
                    match.status.value,
                    score,
                    match.a_row_number or "",
                    match.b_row_number or "",
                    _display_value(match, "a", run.mapping.primary_a),
                    _display_value(match, "b", run.mapping.primary_b),
                    evidence,
                ]
            )
        headers = {"Content-Disposition": f'attachment; filename="rowbridge-{run_id[:8]}.csv"'}
        return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers=headers)

    return app


app = create_app()
