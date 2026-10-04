from __future__ import annotations

import io
import json
import math
import re
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.trustedhost import TrustedHostMiddleware

from rowbridge import __version__
from rowbridge.config import Settings
from rowbridge.exports import build_reconciliation_csv, build_reconciliation_xlsx
from rowbridge.ingestion import (
    MAX_UPLOAD_BYTES,
    InputFileError,
    cleanup_staged_uploads,
    discard_staged_upload,
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
STAGE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
MATCHED_STATUSES = (
    MatchStatus.AUTO_MATCHED,
    MatchStatus.CONFIRMED,
    MatchStatus.MANUAL_MATCHED,
)
MAX_AMOUNT_TOLERANCE = 1_000_000_000.0
MAX_DATE_WINDOW_DAYS = 3650


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


def _statuses_for_view(view: str) -> tuple[MatchStatus, ...] | None:
    if view == "all":
        return None
    if view == "review":
        return (MatchStatus.REVIEW,)
    if view == "matched":
        return MATCHED_STATUSES
    if view == "unmatched":
        return (MatchStatus.UNMATCHED,)
    raise ValueError("Unknown result view")


def _stage_metadata(stage_dir: Path) -> dict[str, str]:
    metadata_path = stage_dir / "metadata.json"
    if not metadata_path.is_file():
        raise InputFileError("Staged upload metadata was not found. Upload the files again.")
    raw = json.loads(metadata_path.read_text(encoding="utf-8"))
    return {str(key): str(value) for key, value in raw.items()}


async def _read_upload_limited(upload: UploadFile, filename: str) -> bytes:
    try:
        content = await upload.read(MAX_UPLOAD_BYTES + 1)
    finally:
        await upload.close()
    if len(content) > MAX_UPLOAD_BYTES:
        raise InputFileError(f"{filename} is larger than 5 MB")
    return content


def _validate_tolerances(amount_tolerance: float, date_window_days: int) -> None:
    if not math.isfinite(amount_tolerance):
        raise ValueError("Amount tolerance must be a finite number")
    if amount_tolerance < 0 or amount_tolerance > MAX_AMOUNT_TOLERANCE:
        raise ValueError(
            f"Amount tolerance must be between 0 and {MAX_AMOUNT_TOLERANCE:,.0f}"
        )
    if date_window_days < 0 or date_window_days > MAX_DATE_WINDOW_DAYS:
        raise ValueError(
            f"Date window must be between 0 and {MAX_DATE_WINDOW_DAYS:,} days"
        )


def _pagination_window(page: int, total_pages: int) -> tuple[int, ...]:
    start = max(1, page - 2)
    end = min(total_pages, page + 2)
    return tuple(range(start, end + 1))



def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings.from_env()
    app_settings.ensure_directories()
    cleanup_staged_uploads(app_settings.upload_dir, app_settings.stage_ttl_seconds)
    repository = Repository(app_settings.database_path)
    repository.initialize()
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
    templates.env.globals["app_version"] = __version__

    app = FastAPI(title="RowBridge", version=__version__)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
    )
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")

    @app.middleware("http")
    async def local_safety_headers(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            # Browser Fetch Metadata is a better fit for this loopback-only app than
            # strict Origin equality. Some browsers/extensions can emit unusual Origin
            # values for local form submissions; Sec-Fetch-Site still distinguishes
            # an ordinary same-origin form POST from a cross-site request.
            fetch_site = request.headers.get("sec-fetch-site", "").lower()
            if fetch_site == "cross-site":
                return HTMLResponse("Cross-site requests are not allowed.", status_code=403)

        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'none'; form-action 'self'; "
            "frame-ancestors 'none'; img-src 'self' data:"
        )
        if not request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={"status_code": exc.status_code, "message": str(exc.detail)},
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> HTMLResponse:
        message = "The submitted form contains an invalid or missing value."
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={"status_code": 422, "message": message},
            status_code=422,
        )

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request=request, name="index.html", context={})

    @app.post("/prepare", response_class=HTMLResponse)
    async def prepare(
        request: Request,
        file_a: Annotated[UploadFile, File()],
        file_b: Annotated[UploadFile, File()],
    ) -> HTMLResponse:
        cleanup_staged_uploads(app_settings.upload_dir, app_settings.stage_ttl_seconds)
        filename_a = file_a.filename or "side-a.csv"
        filename_b = file_b.filename or "side-b.csv"
        try:
            content_a = await _read_upload_limited(file_a, filename_a)
            content_b = await _read_upload_limited(file_b, filename_b)
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
        if STAGE_ID_PATTERN.fullmatch(stage_id) is None:
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
            _validate_tolerances(amount_tolerance, date_window_days)
        except (InputFileError, KeyError, json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

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

        discard_staged_upload(stage_dir)
        return RedirectResponse(url=f"/runs/{run_id}", status_code=303)

    @app.get("/runs", response_class=HTMLResponse)
    def run_history(request: Request, page: int = 1) -> HTMLResponse:
        if page < 1:
            raise HTTPException(status_code=400, detail="Page number must be positive")

        total_runs = repository.count_runs()
        page_size = app_settings.runs_page_size
        total_pages = max(1, math.ceil(total_runs / page_size))
        if page > total_pages:
            raise HTTPException(status_code=404, detail="Run history page not found")

        runs = repository.list_runs(
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        return templates.TemplateResponse(
            request=request,
            name="runs.html",
            context={
                "runs": runs,
                "page": page,
                "total_pages": total_pages,
                "total_runs": total_runs,
                "pagination_pages": _pagination_window(page, total_pages),
            },
        )

    @app.get("/runs/{run_id}/delete", response_class=HTMLResponse)
    def confirm_delete_run(request: Request, run_id: str) -> HTMLResponse:
        run = repository.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return templates.TemplateResponse(
            request=request,
            name="delete_run.html",
            context={"run": run},
        )

    @app.post("/runs/{run_id}/delete")
    def delete_run(run_id: str) -> RedirectResponse:
        if not repository.delete_run(run_id):
            raise HTTPException(status_code=404, detail="Run not found")
        return RedirectResponse(url="/runs", status_code=303)

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def results(
        request: Request,
        run_id: str,
        view: str = "all",
        page: int = 1,
    ) -> HTMLResponse:
        if page < 1:
            raise HTTPException(status_code=400, detail="Page number must be positive")
        run = repository.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        try:
            statuses = _statuses_for_view(view)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        total_results = repository.count_matches(run_id, statuses)
        page_size = app_settings.results_page_size
        total_pages = max(1, math.ceil(total_results / page_size))
        if total_results and page > total_pages:
            raise HTTPException(status_code=404, detail="Result page not found")
        visible_matches = repository.list_matches(
            run_id,
            statuses,
            limit=page_size,
            offset=(page - 1) * page_size,
        )

        unmatched_a_count = repository.count_unmatched_side(run_id, "a")
        unmatched_b_count = repository.count_unmatched_side(run_id, "b")
        manual_row_mode = (
            unmatched_a_count > app_settings.manual_link_select_limit
            or unmatched_b_count > app_settings.manual_link_select_limit
        )
        unmatched_a = (
            ()
            if manual_row_mode
            else repository.list_unmatched_side(
                run_id,
                "a",
                limit=app_settings.manual_link_select_limit,
            )
        )
        unmatched_b = (
            ()
            if manual_row_mode
            else repository.list_unmatched_side(
                run_id,
                "b",
                limit=app_settings.manual_link_select_limit,
            )
        )

        return templates.TemplateResponse(
            request=request,
            name="results.html",
            context={
                "run": run,
                "summary": repository.get_summary(run_id),
                "matches": visible_matches,
                "events": repository.list_review_events(run_id, limit=100),
                "unmatched_a": unmatched_a,
                "unmatched_b": unmatched_b,
                "unmatched_a_count": unmatched_a_count,
                "unmatched_b_count": unmatched_b_count,
                "manual_row_mode": manual_row_mode,
                "active_view": view,
                "page": page,
                "total_pages": total_pages,
                "total_results": total_results,
                "pagination_pages": _pagination_window(page, total_pages),
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
        a_match_id: Annotated[int | None, Form()] = None,
        b_match_id: Annotated[int | None, Form()] = None,
        a_row_number: Annotated[int | None, Form()] = None,
        b_row_number: Annotated[int | None, Form()] = None,
    ) -> RedirectResponse:
        if repository.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found")

        if a_match_id is None or b_match_id is None:
            if a_row_number is None or b_row_number is None:
                raise HTTPException(
                    status_code=400,
                    detail="Choose one unmatched row from each side",
                )
            a_match = repository.get_unmatched_match_by_row(run_id, "a", a_row_number)
            b_match = repository.get_unmatched_match_by_row(run_id, "b", b_row_number)
            if a_match is None or b_match is None:
                raise HTTPException(
                    status_code=404,
                    detail="One of the requested unmatched source rows was not found",
                )
            a_match_id = a_match.id
            b_match_id = b_match.id

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
