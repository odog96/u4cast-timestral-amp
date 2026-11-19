"""Pick where the model runs. Callers get a forecaster and need not know which one."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from u4cast.endpoint import WorkbenchModelForecaster
from u4cast.model import Forecaster, InProcessForecaster
from u4cast.settings import WORKBENCH, Settings

logger = logging.getLogger(__name__)

MARKER = "u4cast"
"""A workbench model is offered for forecasting when its description has this word."""


@dataclass(frozen=True)
class ModelChoice:
    """One model a forecast can be made with."""

    key: str
    """What the user picks: the workbench model's name, or the model's own name."""
    forecaster: Forecaster
    default: bool = False

    @property
    def model_name(self) -> str:
        return self.forecaster.model_name


def served_name(description: str | None) -> str | None:
    """Return the model a workbench model says it serves: "Serves mistralai/Timestral-28M"."""
    found = re.search(r"Serves ([\w./-]*\w)", description or "")
    return found.group(1) if found else None


def workbench_choices(settings: Settings, models: list[Any]) -> list[ModelChoice]:
    """Turn the workbench models of a project into the choices a forecast has.

    A model is offered when its description has the word u4cast, which the
    deploy step writes, or when it is the one the settings name.
    """
    choices = []
    for model in models:
        mine = model.access_key == settings.access_key
        if not model.access_key or not (mine or MARKER in (model.description or "").lower()):
            continue
        name = served_name(model.description) or (settings.model_name if mine else model.name)
        forecaster = WorkbenchModelForecaster(
            settings.endpoint,
            model.access_key,
            api_key=settings.api_key,
            model_name=name,
            batch_size=settings.batch_size,
        )
        choices.append(ModelChoice(key=model.name, forecaster=forecaster, default=mine))
    return sorted(choices, key=lambda choice: (not choice.default, choice.key))


def model_choices(settings: Settings | None = None) -> list[ModelChoice]:
    """Return the models a forecast can be made with, the default one first.

    With the workbench model endpoint, these are the project's workbench
    models that serve forecasts for this application. Otherwise it is the
    one model loaded in this process.
    """
    settings = settings or Settings.from_environment()
    default = forecaster_from_settings(settings)
    alone = [ModelChoice(key=default.model_name.split("/")[-1], forecaster=default, default=True)]
    if settings.serving != WORKBENCH or "CDSW_PROJECT_ID" not in os.environ:
        return alone
    try:
        import cmlapi

        client = cmlapi.default_client()
        models = client.list_models(os.environ["CDSW_PROJECT_ID"], page_size=100).models
        return workbench_choices(settings, models) or alone
    except Exception:
        logger.warning("Could not list the workbench models; offering the default", exc_info=True)
        return alone


def forecaster_from_settings(settings: Settings | None = None) -> Forecaster:
    """Return the forecaster the settings ask for.

    That is the Cloudera AI Workbench model endpoint, or Timestral loaded in
    this process. A later implementation, such as Cloudera AI Inference
    Service, is added here.
    """
    settings = settings or Settings.from_environment()
    if settings.serving == WORKBENCH:
        if not settings.endpoint or not settings.access_key:
            raise ValueError(
                "Serving from the workbench model needs U4CAST_MODEL_ACCESS_KEY, and "
                "U4CAST_MODEL_ENDPOINT when not running in Cloudera AI."
            )
        return WorkbenchModelForecaster(
            settings.endpoint,
            settings.access_key,
            api_key=settings.api_key,
            model_name=settings.model_name,
            batch_size=settings.batch_size,
        )
    return InProcessForecaster(settings.model_name, settings.batch_size)
