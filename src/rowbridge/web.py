from __future__ import annotations

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
from rowbridge.exports import build_reconciliation_csv, build_reconciliation_xlsx
from rowbridge.ingestion import (
    InputFileError,
    parse_input_bytes,
    preview_rows,
    read_staged_input,
    staged_filename,
    write_staged_input,
)
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


def _table_details(table: CsvTable) -> str:
    if table.source_format == "xlsx":
        return f"Excel workbook · sheet {table.sheet_name or 'first data sheet'}"
    delimiter_names = {",": "comma", ";": "semicolon", "\t": "tab", "|": "pipe"}
    delimiter = delimiter_names.get(table.delimiter or ",", repr(table.delimiter or ","))
    return f"CSV · {table.encoding or 'unknown encoding'} · {delimiter} delimiter"


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


def _stage_metadata(stage_dir: Path) -> dict[str, str]:
    metadata_path = stage_dir / "metadata.json"
    if not metadata_path.is_file():
        raise InputFileError("Staged upload metadata was not found. Upload the files again.")
    raw = json.loads(metadata_path.read_text(encoding="utf-8"))
    return {str(key): str(value) for key, value in raw.items()}


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings.from_env()
    app_settings.ensure_directories()
    repository = Repository(app_settings.database_path)
    repository.initialize()
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")

    app = FastAPI(title="RowBridge", version="0.4.0")
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
        content_a = await file_a.read()
        content_b = await file_b.read()
        try:
            table_a = parse_input_bytes(content_a, filename_a)
            table_b = parse_input_bytes(content_b, filename_b)
            staged_a = staged_filename("a", filename_a)
            staged_b = staged_filename("b", filename_b)
        except InputFileError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        stage_id = uuid4().hex
        stage_dir = app_settings.upload_dir / stage_id
        write_staged_input(stage_dir / staged_a, content_a)
        write_staged_input(stage_dir / staged_b, content_b)
        (stage_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "filename_a": filename_a,
                    "filename_b": filename_b,
                    "staged_a": staged_a,
                    "staged_b": staged_b,
                }
            ),
            encoding="utf-8",
        )

        return templates.TemplateResponse(
            request=request,
            name="mapping.html",
            context={
                "stage_id": stage_id,
                "table_a": table_a,
                "table_b": table_b,
                "preview_a": preview_rows(table_a),
                "preview_b": preview_rows(table_b),
                "details_a": _table_details(table_a),
                "details_b": _table_details(table_b),
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
            metadata = _stage_metadata(stage_dir)
            filename_a = metadata["filename_a"]
            filename_b = metadata["filename_b"]
            staged_a = metadata.get("staged_a", "a.csv")
            staged_b = metadata.get("staged_b", "b.csv")
            table_a = read_staged_input(stage_dir / staged_a, filename_a)
            table_b = read_staged_input(stage_dir / staged_b, filename_b)
        except (InputFileError, KeyError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

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
        content = "\ufeff" + build_reconciliation_csv(run, repository.list_matches(run_id))
        headers = {"Content-Disposition": f'attachment; filename="rowbridge-{run_id[:8]}.csv"'}
        return StreamingResponse(
            iter([content]),
            media_type="text/csv; charset=utf-8",
            headers=headers,
        )

    @app.get("/runs/{run_id}/export.xlsx")
    def export_xlsx(run_id: str) -> StreamingResponse:
        run = repository.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        content = build_reconciliation_xlsx(
            run,
            repository.list_matches(run_id),
            repository.list_review_events(run_id),
        )
        headers = {
            "Content-Disposition": f'attachment; filename="rowbridge-{run_id[:8]}.xlsx"'
        }
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return StreamingResponse(io.BytesIO(content), media_type=media_type, headers=headers)

    return app


app = create_app()
