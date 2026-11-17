"""Pick where the model runs. Callers get a forecaster and need not know which one."""

from __future__ import annotations

from u4cast.endpoint import WorkbenchModelForecaster
from u4cast.model import Forecaster, InProcessForecaster
from u4cast.settings import WORKBENCH, Settings


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
