"""Start the application: `python -m webapp`.

It listens on the address and port Cloudera AI gives an application:
127.0.0.1 and the environment variable `CDSW_APP_PORT`.
"""

from __future__ import annotations

import logging
import os

import uvicorn

from u4cast.connections import ClouderaCatalog
from u4cast.production import ClouderaScheduler
from u4cast.serving import forecaster_from_settings, model_choices
from u4cast.storage import Store, spark_session
from webapp.api import create_app
from webapp.workspace import Workspace


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    workspace = Workspace(
        lambda: Store(spark_session()),
        forecaster_from_settings(),
        model_choices,
        lambda store: ClouderaCatalog(lambda statement: store.spark.sql(statement).collect()),
        ClouderaScheduler(),
    )
    workspace.start()
    uvicorn.run(
        create_app(workspace),
        host=os.environ.get("U4CAST_HOST", "127.0.0.1"),
        port=int(os.environ.get("CDSW_APP_PORT", "8090")),
        log_level="info",
    )


if __name__ == "__main__":
    main()
