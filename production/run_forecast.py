"""One run of a forecast in production: issue it again from the history as it stands.

The job of a forecast in production runs this on its schedule. It reads the
history table, which the owner's own pipeline keeps up to date, and appends
the new issue to the dataset's forecast values table. It does not go through
the application, so it runs whether or not the application is open.

Three things can happen, and each is written to the table `production_runs`:

- new history has arrived: a new issue is made;
- no history has arrived since the last issue: nothing is made, and the run
  says the data is late;
- something fails, for example the forecast's model is not deployed: the run
  says why and the job ends as failed, so Cloudera AI can send its alert.

Which forecast: the environment variables `U4CAST_PRODUCTION_DATASET` and
`U4CAST_PRODUCTION_FORECAST`, set on the job when the forecast is promoted.
"""

from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.getcwd())

from u4cast.production import DATASET_VARIABLE, FAILED, FORECAST_VARIABLE
from u4cast.serving import forecaster_from_settings, model_choices
from u4cast.storage import Store, spark_session
from webapp.workspace import Workspace

logger = logging.getLogger("u4cast.production")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    dataset, name = os.environ[DATASET_VARIABLE], os.environ[FORECAST_VARIABLE]
    workspace = Workspace(lambda: Store(spark_session()), forecaster_from_settings(), model_choices)
    workspace.connect()
    if workspace.state != "ready":
        raise SystemExit(f"The data lake could not be reached: {workspace.error}")
    run = workspace.run_scheduled(dataset, name)
    logger.info("%s: %s. %s (%.1f s)", name, run["outcome"], run["detail"], run["seconds"])
    if run["outcome"] == FAILED:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
