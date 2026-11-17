"""Timestral behind a Cloudera AI Workbench model endpoint.

A workbench model is a Python function Cloudera AI serves as a web endpoint.
This module holds both ends of that call, so they cannot drift apart:
`handle_request` is what the served function runs, and
`WorkbenchModelForecaster` is the forecaster that calls it.

What shapes the call, from Cloudera's documentation and first build check 6:
a request is cut off after 30 seconds, a request should stay under 5 MB, and
a replica answers one request at a time. So series are sent in batches sized
by the work they cost, not only by their number.
"""

from __future__ import annotations

import json
import logging
import math
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

import numpy as np

from u4cast.model import BatchedForecaster, Forecaster, ModelForecast
from u4cast.settings import DEFAULT_BATCH_SIZE, DEFAULT_MODEL_NAME

logger = logging.getLogger(__name__)

# Timestral reads history and writes forecasts in blocks of 32 steps, and its
# running time grows with series x blocks.
BLOCK_STEPS = 32
# Series x blocks allowed in one request. Sized to stay well inside the
# endpoint's 30 seconds on 4 vCPU; see docs/decisions.md, check 6.
DEFAULT_WORK_BUDGET = 2000
# Seconds to wait for an answer. Longer than the endpoint's own limit, so the
# endpoint's error is the one that is reported.
DEFAULT_TIMEOUT = 60.0
# Statuses that mean "try again shortly": the replica is busy or restarting.
_RETRY_STATUSES = (429, 502, 503)
_SIGNIFICANT_DIGITS = 7  # what a 32-bit float holds; keeps requests small

PostJson = Callable[[str, dict[str, Any], dict[str, str], float], dict[str, Any]]


class ModelEndpointError(RuntimeError):
    """The model endpoint could not be reached or did not return a forecast."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _to_lists(values: np.ndarray) -> list[list[float | None]]:
    """Write an array as JSON-ready rows, with None for NaN."""
    return [
        [None if value != value else float(f"{value:.{_SIGNIFICANT_DIGITS}g}") for value in row]
        for row in values.tolist()
    ]


def _to_array(rows: Any, name: str) -> np.ndarray:
    """Read JSON rows back into an array, with NaN for None."""
    try:
        array = np.array(rows, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be rows of numbers of equal length.") from error
    if array.ndim != 2:
        raise ValueError(f"{name} must be rows of numbers of equal length.")
    return array


def handle_request(forecaster: Forecaster, args: Mapping[str, Any]) -> dict[str, Any]:
    """Answer one request to the endpoint. The served function calls this.

    `args` holds `histories`, rows of numbers with null for a missing value,
    and `horizon`. The answer holds `forecast`, `low` and `high` as rows, the
    quantile levels of low and high, the model's name, and the seconds the
    model took.
    """
    if "histories" not in args or "horizon" not in args:
        raise ValueError("A request needs histories and horizon.")
    histories = _to_array(args["histories"], "histories")
    started = time.perf_counter()
    result = forecaster.forecast(histories, int(args["horizon"]))
    return {
        "forecast": _to_lists(result.forecast),
        "low": _to_lists(result.low),
        "high": _to_lists(result.high),
        "low_level": result.low_level,
        "high_level": result.high_level,
        "model_name": forecaster.model_name,
        "seconds": round(time.perf_counter() - started, 3),
    }


def _post_json(
    url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:300]
        raise ModelEndpointError(
            f"The model endpoint answered with status {error.code}: {detail}", error.code
        ) from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise ModelEndpointError(f"The model endpoint could not be reached: {error}") from error


class WorkbenchModelForecaster(BatchedForecaster):
    """Timestral served by a Cloudera AI Workbench model endpoint.

    `access_key` says which model to call. `api_key` is the caller's Cloudera
    AI API key, needed when the model has authentication on. `model_name` is
    replaced by the name the endpoint reports once it has answered.
    """

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        *,
        api_key: str | None = None,
        model_name: str = DEFAULT_MODEL_NAME,
        batch_size: int = DEFAULT_BATCH_SIZE,
        work_budget: int = DEFAULT_WORK_BUDGET,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = 2,
        post: PostJson = _post_json,
    ) -> None:
        if not endpoint or not access_key:
            raise ValueError("The workbench model needs an endpoint address and an access key.")
        if batch_size < 1 or work_budget < 1:
            raise ValueError("The batch size and the work budget must be at least 1.")
        self.endpoint = endpoint
        self.model_name = model_name
        self.batch_size = batch_size
        self.work_budget = work_budget
        self.timeout = timeout
        self.retries = retries
        self._access_key = access_key
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._post = post

    def _batch_size_for(self, steps: int, horizon: int) -> int:
        blocks = math.ceil(steps / BLOCK_STEPS) + math.ceil(horizon / BLOCK_STEPS)
        return max(1, min(self.batch_size, self.work_budget // blocks))

    def _forecast_batch(self, histories: np.ndarray, horizon: int) -> ModelForecast:
        payload = {
            "accessKey": self._access_key,
            "request": {"histories": _to_lists(histories), "horizon": horizon},
        }
        answer = self._call(payload)
        if not answer.get("success", False) or "response" not in answer:
            detail = json.dumps(answer.get("errors", answer))[:300]
            raise ModelEndpointError(f"The model endpoint did not return a forecast: {detail}")
        response = answer["response"]
        expected = (histories.shape[0], horizon)
        arrays = {name: _to_array(response[name], name) for name in ("forecast", "low", "high")}
        for name, array in arrays.items():
            if array.shape != expected:
                raise ModelEndpointError(
                    f"The model endpoint returned {name} of shape {array.shape}; "
                    f"expected {expected}."
                )
        self.model_name = str(response.get("model_name", self.model_name))
        return ModelForecast(
            low_level=float(response["low_level"]),
            high_level=float(response["high_level"]),
            **arrays,
        )

    def _call(self, payload: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(self.retries + 1):
            try:
                return self._post(self.endpoint, payload, self._headers, self.timeout)
            except ModelEndpointError as error:
                if error.status not in _RETRY_STATUSES or attempt == self.retries:
                    raise
                wait = 2.0 * (attempt + 1)
                logger.warning(
                    "Model endpoint answered %s; trying again in %.0f seconds", error.status, wait
                )
                time.sleep(wait)
        raise AssertionError("unreachable")
