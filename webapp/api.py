"""The web API and the page that serves the front end.

`create_app` takes a `Workspace`, so tests can pass one that needs no Spark
and no model. Every route under `/api` answers with JSON. Anything else
returns the front end, which does its own routing.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from webapp.jobs import JobRunner
from webapp.workspace import NotReadyError, SliceSpec, UserError, Workspace

logger = logging.getLogger(__name__)

FRONT_END = Path(__file__).resolve().parent.parent / "frontend" / "dist"


class SliceRequest(BaseModel):
    """Which series, and which stretch of their history."""

    dimension_columns: list[str]
    slice_filter: dict[str, list[Any]] = Field(default_factory=dict)
    history_start: str | None = None
    history_end: str | None = None
    minimum_history: int | None = None
    target_column: str | None = None
    horizon: int = 12
    cutoffs: int = 0
    cutoff_step: int = 1

    def spec(self, dataset: str) -> SliceSpec:
        return SliceSpec(
            dataset=dataset,
            dimension_columns=tuple(self.dimension_columns),
            slice_filter={k: v for k, v in self.slice_filter.items() if v},
            history_start=self.history_start,
            history_end=self.history_end,
            minimum_history=self.minimum_history,
            target_column=self.target_column or None,
        )


class ForecastRequest(SliceRequest):
    forecast_name: str


class DatasetRequest(BaseModel):
    name: str
    source_table: str
    date_column: str
    target_column: str
    dimension_columns: list[str]
    frequency: str
    fill: str = "missing"


class ReissueRequest(BaseModel):
    as_of: str | None = None


class AccuracyRequest(BaseModel):
    cutoffs: int = 4
    cutoff_step: int = 1


def create_app(workspace: Workspace, front_end: Path = FRONT_END) -> FastAPI:
    """Build the web application around a workspace."""
    app = FastAPI(title="u4cast", docs_url="/api/docs", openapi_url="/api/openapi.json")
    jobs = JobRunner()
    app.state.workspace = workspace
    app.state.jobs = jobs

    @app.exception_handler(UserError)
    async def user_error(_: Request, error: UserError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.exception_handler(ValueError)
    async def value_error(_: Request, error: ValueError) -> JSONResponse:
        # The forecasting package words its errors for the user.
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.exception_handler(NotReadyError)
    async def not_ready(_: Request, error: NotReadyError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=503)

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return {**workspace.status(), "jobs": [job.to_json() for job in jobs.active()]}

    # Datasets

    @app.get("/api/datasets")
    def datasets() -> list[dict[str, Any]]:
        return workspace.datasets()

    @app.post("/api/datasets")
    def save_dataset(body: DatasetRequest) -> dict[str, Any]:
        return workspace.save_dataset(body.model_dump())

    @app.get("/api/datasets/{name}")
    def dataset(name: str) -> dict[str, Any]:
        return workspace.dataset_overview(name)

    @app.get("/api/datasets/{name}/history")
    def history(name: str, target: str | None = None) -> dict[str, Any]:
        return workspace.history(name, target or None)

    @app.get("/api/datasets/{name}/dimensions/{column}")
    def dimension_values(name: str, column: str) -> list[Any]:
        return workspace.dimension_values(name, column)

    @app.post("/api/datasets/{name}/slice")
    def slice_summary(name: str, body: SliceRequest) -> dict[str, Any]:
        return workspace.slice_summary(
            body.spec(name), body.horizon, body.cutoffs, body.cutoff_step
        )

    # Sources

    @app.get("/api/sources")
    def sources() -> list[dict[str, Any]]:
        return workspace.source_tables()

    @app.get("/api/sources/profile")
    def profile(table: str, date_column: str | None = None) -> dict[str, Any]:
        return workspace.profile(table, date_column)

    @app.post("/api/uploads")
    async def upload(file: UploadFile = File(...)) -> dict[str, Any]:  # noqa: B008
        content = await file.read()
        return workspace.upload(file.filename or "upload.csv", content)

    # Forecasts

    @app.post("/api/datasets/{name}/forecasts")
    def create_forecast(name: str, body: ForecastRequest) -> dict[str, Any]:
        workspace.check_new_forecast(name, body.forecast_name, body.horizon)
        spec = body.spec(name)
        job = jobs.submit(
            f"Forecast {body.forecast_name}",
            lambda job: workspace.create_forecast(
                job, spec, body.forecast_name, body.horizon, body.cutoffs, body.cutoff_step
            ),
        )
        return job.to_json()

    @app.get("/api/datasets/{name}/forecasts/{forecast_name}")
    def forecast(name: str, forecast_name: str) -> dict[str, Any]:
        return workspace.forecast_overview(name, forecast_name)

    @app.post("/api/datasets/{name}/forecasts/{forecast_name}/reissue")
    def reissue(name: str, forecast_name: str, body: ReissueRequest) -> dict[str, Any]:
        workspace.dataset(name)
        job = jobs.submit(
            f"Reissue {forecast_name}",
            lambda job: workspace.reissue(job, name, forecast_name, body.as_of),
        )
        return job.to_json()

    @app.post("/api/datasets/{name}/forecasts/{forecast_name}/accuracy")
    def check_accuracy(name: str, forecast_name: str, body: AccuracyRequest) -> dict[str, Any]:
        workspace.dataset(name)
        job = jobs.submit(
            f"Check accuracy of {forecast_name}",
            lambda job: workspace.check_accuracy(
                job, name, forecast_name, body.cutoffs, body.cutoff_step
            ),
        )
        return job.to_json()

    @app.get("/api/datasets/{name}/forecasts/{forecast_name}/accuracy")
    def accuracy(
        name: str, forecast_name: str, q: str = "", limit: int = Query(100, ge=1, le=1000)
    ) -> dict[str, Any]:
        return workspace.accuracy(name, forecast_name, q, limit)

    @app.get("/api/datasets/{name}/forecasts/{forecast_name}/compare")
    def compare(
        name: str, forecast_name: str, ts_id: str | None = None, kind: str = "forecast"
    ) -> dict[str, Any]:
        return workspace.compare(name, forecast_name, ts_id or None, kind)

    @app.get("/api/forecasts/{forecast_id}/chart")
    def chart(forecast_id: str, ts_id: str | None = None) -> dict[str, Any]:
        return workspace.chart(forecast_id, ts_id or None)

    @app.get("/api/forecasts/{forecast_id}/series")
    def series(
        forecast_id: str,
        q: str = "",
        sort: str = "forecast",
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        return workspace.series(forecast_id, q, sort, limit, offset)

    @app.get("/api/forecasts/{forecast_id}/rows")
    def stored_rows(forecast_id: str, limit: int = Query(25, ge=1, le=500)) -> dict[str, Any]:
        return workspace.stored_rows(forecast_id, limit)

    @app.get("/api/forecasts/{forecast_id}/download.csv")
    def download(forecast_id: str) -> Response:
        text, filename = workspace.csv(forecast_id)
        return Response(
            text,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str) -> dict[str, Any]:
        found = jobs.get(job_id)
        if found is None:
            raise HTTPException(404, "There is no job with that identifier.")
        return found.to_json()

    @app.get("/api/{path:path}")
    def unknown_api(path: str) -> JSONResponse:
        return JSONResponse({"detail": f"There is no /api/{path}."}, status_code=404)

    # The front end. Its files are built by `npm run build` into frontend/dist.

    if (front_end / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=front_end / "assets"), name="assets")

    @app.get("/{path:path}")
    def page(path: str) -> Response:
        file = front_end / path
        if path and file.is_file() and front_end in file.resolve().parents:
            return FileResponse(file)
        index = front_end / "index.html"
        if index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return Response(
            "The front end is not built. Run `npm install` and `npm run build` in frontend/.",
            media_type="text/plain",
            status_code=503,
        )

    return app
