"""Import endpoint: upload a CSV/JSON export of reviews."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from reviewhub.collection.status import block_reason, collection_status, strategy_of
from reviewhub.core.errors import problem
from reviewhub.ingestion.contracts import ForbiddenSource, ImportReport
from reviewhub.ingestion.service import ImportOptions, import_file
from reviewhub.persistence.runs import RunAlreadyRunning
from reviewhub.persistence.scopes import DatasetIsolationError

router = APIRouter(tags=["ingestion"])


@router.post(
    "/ingestion/imports",
    response_model=ImportReport,
    responses={
        409: {"description": "Same file already importing, or dataset-kind clash"},
        422: {"description": "File rejected"},
    },
)
def create_import(
    request: Request,
    file: Annotated[UploadFile, File(description="CSV or JSON export of reviews")],
    property: Annotated[
        str | None, Form(description="Property slug/name when the file has no property column")
    ] = None,
    dry_run: Annotated[bool, Form(description="Validate and report; write nothing")] = False,
    date_format: Annotated[str | None, Form(description="strptime format, e.g. %d/%m/%Y")] = None,
    encoding: Annotated[str | None, Form()] = None,
    id_namespace: Annotated[str, Form()] = "booking",
    format: Annotated[str, Form(description="auto, csv or json")] = "auto",
) -> ImportReport | JSONResponse:
    settings = request.app.state.settings
    content = file.file.read(settings.import_max_bytes + 1)
    options = ImportOptions(
        property=property,
        dry_run=dry_run,
        encoding=encoding,
        date_format=date_format,
        id_namespace=id_namespace,
        format=format,
        trigger="api",
    )
    try:
        report = import_file(
            request.app.state.session_factory,
            content=content,
            filename=file.filename or "upload",
            options=options,
            stale_after=timedelta(minutes=settings.run_stale_after_minutes),
            max_bytes=settings.import_max_bytes,
        )
    except RunAlreadyRunning as exc:
        return problem(
            409, "This file is already being imported", detail=str(exc), run_id=exc.run_id
        )
    except DatasetIsolationError as exc:
        return problem(409, "Dataset isolation", detail=str(exc))
    except ForbiddenSource as exc:
        return problem(400, "Source not allowed", detail=str(exc))
    if report.file_error is not None:
        return problem(
            422,
            "File rejected",
            code=report.file_error.code,
            detail=report.file_error.message,
            report=report.model_dump(mode="json"),
        )
    return report


@router.get("/ingestion/runs")
def list_runs(
    request: Request,
    page: int = 1,
    page_size: int = 10,
    kind: str | None = None,
    all: bool = False,
) -> dict[str, object]:
    """Run history (collection, import, analysis) for the Data page.

    By default, internal steps are hidden: the import that saves a collection run's pages
    (already summarised on the collection run) and analysis runs that found nothing to do.
    ``all=true`` returns everything."""
    from sqlalchemy import func, select

    from reviewhub.persistence.models import Property, Run, RunProperty

    page, page_size = max(1, page), min(max(1, page_size), 50)
    with request.app.state.session_factory() as s:
        q = select(Run).order_by(Run.id.desc())
        if kind:
            q = q.where(Run.kind == kind)
        if not all:
            q = q.where(
                ~((Run.kind == "import") & (Run.trigger == "collect")),
                ~(
                    (Run.kind == "analyse")
                    & (func.coalesce(Run.items_total, 0) == 0)
                    & (Run.status == "success")
                ),
            )
        total = s.scalar(select(func.count()).select_from(q.subquery())) or 0
        runs = s.scalars(q.limit(page_size).offset((page - 1) * page_size)).all()
        names = {p.id: p.name for p in s.scalars(select(Property))}
        data = []
        for r in runs:
            props = s.scalars(select(RunProperty).where(RunProperty.run_id == r.id)).all()
            data.append(
                {
                    "id": r.id,
                    "kind": r.kind,
                    "source": r.source,
                    "mode": r.mode,
                    "trigger": r.trigger,
                    "strategy": strategy_of(r) if r.kind == "collect" else None,
                    "status": r.status,
                    "block_reason": block_reason(r.error) if r.status == "blocked" else None,
                    "version": r.version,
                    "started_at": r.started_at,
                    "finished_at": r.finished_at,
                    "items_total": r.items_total,
                    "items_ok": r.items_ok,
                    "items_failed": r.items_failed,
                    "error": r.error,
                    "tokens": {"input": r.input_tokens, "output": r.output_tokens},
                    "properties": [
                        {
                            "property": names.get(p.property_id),
                            "status": p.status,
                            "pages": p.pages,
                            "received": p.received,
                            "new": p.new,
                            "updated": p.updated,
                            "unchanged": p.unchanged,
                            "rejected": p.rejected,
                            "booking_reported_total": p.reported_headline_total,
                            "error": p.error,
                            "block_reason": block_reason(p.error)
                            if p.status == "blocked"
                            else None,
                        }
                        for p in props
                    ],
                }
            )
    return {"data": data, "meta": {"page": page, "page_size": page_size, "total": total}}


@router.get("/ingestion/collection-status")
def get_collection_status(request: Request) -> dict[str, object]:
    """Per-property and per-strategy collection reliability (last attempt, BLOCKED reason,
    last success, next eligible attempt, published vs collected counts). Read-only."""
    from datetime import UTC, datetime

    with request.app.state.session_factory() as s:
        return collection_status(s, request.app.state.settings, datetime.now(UTC))


@router.get("/properties")
def list_properties(request: Request) -> dict[str, object]:
    """The four properties (for filters and for mapping an import file to a property)."""
    from sqlalchemy import select

    from reviewhub.persistence.models import Property

    with request.app.state.session_factory() as s:
        rows = s.scalars(select(Property).order_by(Property.id)).all()
        return {
            "data": [
                {
                    "id": p.id,
                    "slug": p.slug,
                    "name": p.name,
                    "booking_url": p.booking_url,
                    "timezone": p.timezone,
                    "active": p.active,
                }
                for p in rows
            ]
        }
