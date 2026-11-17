"""Settings read from environment variables, so they change without a code change."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

DEFAULT_MODEL_NAME = "mistralai/Timestral-209M"
# The most series in one model call. Batch size does not change speed or
# results. A workbench model endpoint lowers it further for long histories.
DEFAULT_BATCH_SIZE = 500

WORKBENCH = "workbench"
IN_PROCESS = "in_process"


@dataclass(frozen=True)
class Settings:
    """Which model is used and where it runs.

    Environment variables:

    - `U4CAST_MODEL_NAME`: the Timestral model, by its Hugging Face name.
    - `U4CAST_BATCH_SIZE`: the most series in one model call.
    - `U4CAST_SERVING`: `workbench` to call a Cloudera AI Workbench model
      endpoint, `in_process` to load the model in this process. Without it,
      the endpoint is used when an access key is set.
    - `U4CAST_MODEL_ACCESS_KEY`: the access key of the workbench model.
    - `U4CAST_MODEL_ENDPOINT`: the address of the endpoint. Without it, the
      address is built from `CDSW_DOMAIN`, which Cloudera AI sets.
    - `CDSW_APIV2_KEY`: the caller's Cloudera AI API key, which Cloudera AI
      sets. Sent to the endpoint, which checks it.
    """

    model_name: str = DEFAULT_MODEL_NAME
    batch_size: int = DEFAULT_BATCH_SIZE
    serving: str = IN_PROCESS
    endpoint: str | None = None
    access_key: str | None = field(default=None, repr=False)
    api_key: str | None = field(default=None, repr=False)

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Read the settings from the environment, with defaults where one exists."""
        environ = os.environ if environ is None else environ
        batch_size = int(environ.get("U4CAST_BATCH_SIZE", DEFAULT_BATCH_SIZE))
        if batch_size < 1:
            raise ValueError(f"U4CAST_BATCH_SIZE must be at least 1; got {batch_size}.")
        access_key = environ.get("U4CAST_MODEL_ACCESS_KEY") or None
        serving = environ.get("U4CAST_SERVING") or (WORKBENCH if access_key else IN_PROCESS)
        if serving not in (WORKBENCH, IN_PROCESS):
            raise ValueError(f"U4CAST_SERVING must be {WORKBENCH} or {IN_PROCESS}; got {serving}.")
        endpoint = environ.get("U4CAST_MODEL_ENDPOINT") or None
        if endpoint is None and environ.get("CDSW_DOMAIN"):
            endpoint = f"https://modelservice.{environ['CDSW_DOMAIN']}/model"
        return cls(
            model_name=environ.get("U4CAST_MODEL_NAME", DEFAULT_MODEL_NAME),
            batch_size=batch_size,
            serving=serving,
            endpoint=endpoint,
            access_key=access_key,
            api_key=environ.get("CDSW_APIV2_KEY") or None,
        )
