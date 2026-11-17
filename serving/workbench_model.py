"""The function Cloudera AI Workbench serves as the Timestral model endpoint.

Cloudera AI imports this file once when a replica starts, which loads the
model, and then calls `forecast` for every request. The request and the
answer are described in `u4cast.endpoint.handle_request`.
"""

from __future__ import annotations

import logging
import math
import os
from typing import Any

import cml.models_v1 as cml_models
import torch

from u4cast.endpoint import handle_request
from u4cast.model import InProcessForecaster
from u4cast.settings import Settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
logger = logging.getLogger("u4cast.workbench_model")


def _processors() -> int | None:
    """Return how many processors this replica may use, if Cloudera AI says so.

    torch counts the processors of the whole host. Using more threads than the
    replica is allowed makes it slower, not faster.
    """
    if os.environ.get("U4CAST_TORCH_THREADS"):
        return int(os.environ["U4CAST_TORCH_THREADS"])
    try:
        with open("/sys/fs/cgroup/cpu.max") as limit_file:
            quota, period = limit_file.read().split()
        if quota != "max":
            return max(1, math.ceil(int(quota) / int(period)))
    except (OSError, ValueError):
        pass
    if os.environ.get("CDSW_CPU_MILLICORES"):
        return max(1, math.ceil(int(os.environ["CDSW_CPU_MILLICORES"]) / 1000))
    return None


_threads = _processors()
if _threads:
    torch.set_num_threads(_threads)
_settings = Settings.from_environment()
_forecaster = InProcessForecaster(_settings.model_name, _settings.batch_size)
_forecaster.load()
logger.info("Serving %s on %d threads", _settings.model_name, torch.get_num_threads())


# Cloudera AI requires this decorator on a served function. It reads the
# request, calls the function, and reports an error the function raises.
@cml_models.cml_model
def forecast(args: dict[str, Any]) -> dict[str, Any]:
    """Forecast `args["horizon"]` steps after each row of `args["histories"]`."""
    answer = handle_request(_forecaster, args)
    answer["threads"] = torch.get_num_threads()
    return answer
