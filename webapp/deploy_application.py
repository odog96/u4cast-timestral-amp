"""Create and start the Cloudera AI application that runs this project's web server.

Run inside Cloudera AI, in a session:

    python3 webapp/deploy_application.py

An AMP does the same through its own application step. This script is for a
project that was set up by hand, and for restarting the application after
the code changed. Running it again updates and restarts the application.

Settings, all optional, from environment variables:

- `U4CAST_APPLICATION`: the name and the first part of the address. Default `u4cast`.
- `U4CAST_RUNTIME_IDENTIFIER`: the runtime image. Default: the newest
  Python 3.12, Standard, PBJ Workbench runtime.
- `U4CAST_RUNTIME_ADDONS`: the runtime add-ons, separated by commas. Default:
  the newest Spark 3.5.1 and the newest Hadoop CLI.
- `U4CAST_APP_CPU`, `U4CAST_APP_MEMORY`: vCPU and GB. Default 4 and 8.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time

import cmlapi

logger = logging.getLogger("u4cast.deploy_application")

SCRIPT = "webapp/launch.py"
POLL_SECONDS = 10
START_TIMEOUT_SECONDS = 15 * 60


def newest_runtime(client: cmlapi.CMLServiceApi) -> str:
    search = json.dumps({"kernel": "Python 3.12", "edition": "Standard", "editor": "PBJ Workbench"})
    runtimes = client.list_runtimes(search_filter=search, page_size=500).runtimes
    if not runtimes:
        raise RuntimeError("This workbench has no Python 3.12, Standard, PBJ Workbench runtime.")
    return max(runtimes, key=lambda runtime: runtime.full_version).image_identifier


def addons(client: cmlapi.CMLServiceApi) -> list[str]:
    """Return the runtime add-ons the application needs: Spark and the Hadoop client.

    The application reads and writes Iceberg tables with Spark. Spark finds
    the data lake's catalog through libraries that come with the Hadoop
    add-on; without it the catalog cannot be reached.
    """
    available: dict[str, list[str]] = {}
    for addon in client.list_runtime_addons(page_size=200).runtime_addons:
        if addon.status == "AVAILABLE":
            available.setdefault(addon.component, []).append(addon.identifier)
    spark = sorted(available.get("Spark", []))
    hadoop = sorted(available.get("HadoopCLI", []))
    if not spark or not hadoop:
        raise RuntimeError("This workbench lacks the Spark or the Hadoop CLI runtime add-on.")
    spark_351 = [identifier for identifier in spark if identifier.startswith("spark351")]
    return [(spark_351 or spark)[-1], hadoop[-1]]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)
    client = cmlapi.default_client()
    project_id = os.environ["CDSW_PROJECT_ID"]
    name = os.environ.get("U4CAST_APPLICATION", "u4cast")
    runtime = os.environ.get("U4CAST_RUNTIME_IDENTIFIER") or newest_runtime(client)
    chosen = os.environ.get("U4CAST_RUNTIME_ADDONS")
    needed = [addon.strip() for addon in chosen.split(",")] if chosen else addons(client)
    cpu = float(os.environ.get("U4CAST_APP_CPU", 4))
    memory = float(os.environ.get("U4CAST_APP_MEMORY", 8))

    applications = client.list_applications(project_id, page_size=100).applications
    existing = [application for application in applications if application.name == name]
    if existing:
        application = existing[0]
        logger.info("Updating and restarting the application %s with %s", name, needed)
        client.update_application(
            cmlapi.Application(
                script=SCRIPT,
                cpu=cpu,
                memory=memory,
                runtime_identifier=runtime,
                runtime_addon_identifiers=needed,
            ),
            project_id,
            application.id,
        )
        client.restart_application(project_id, application.id)
    else:
        logger.info("Creating the application %s on %s with %s", name, runtime, needed)
        # An address is used once in a workbench. If another project already runs
        # this application as "u4cast", this one adds its own project's name.
        project = re.sub(r"[^a-z0-9]+", "-", os.environ.get("CDSW_PROJECT", "").lower()).strip("-")
        application = None
        for subdomain in dict.fromkeys([name, f"{name}-{project}" if project else name]):
            try:
                application = client.create_application(
                    cmlapi.CreateApplicationRequest(
                        project_id=project_id,
                        name=name,
                        subdomain=subdomain,
                        description="Forecast many time series at once with Timestral.",
                        script=SCRIPT,
                        kernel="python3",
                        cpu=cpu,
                        memory=memory,
                        runtime_identifier=runtime,
                        runtime_addon_identifiers=needed,
                    ),
                    project_id,
                )
                break
            except cmlapi.rest.ApiException as error:
                logger.info("The address %s could not be used: %s", subdomain, error.reason)
                failure = error
        if application is None:
            raise failure

    started = time.monotonic()
    last = None
    while True:
        status = client.get_application(project_id, application.id).status
        if status != last:
            logger.info("Application: %s (%.0f s)", status, time.monotonic() - started)
            last = status
        waited = time.monotonic() - started
        if "RUNNING" in status.upper():
            break
        if "FAILED" in status.upper() or ("STOPPED" in status.upper() and waited > 60):
            raise RuntimeError(f"The application ended as {status}.")
        if waited > START_TIMEOUT_SECONDS:
            raise TimeoutError(f"The application was still {status} after {waited:.0f} seconds.")
        time.sleep(POLL_SECONDS)
    address = f"https://{application.subdomain}.{os.environ['CDSW_DOMAIN']}"
    logger.info("The application is running at %s", address)


if __name__ == "__main__":
    main()
