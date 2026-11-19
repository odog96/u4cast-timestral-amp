"""Create, build and deploy the workbench model that serves Timestral.

Run inside Cloudera AI, in a session or as an AMP step:

    python3 serving/deploy_workbench_model.py

It uses Cloudera AI's API. When the model is deployed, it saves the model's
access key in the project's environment variables as
`U4CAST_MODEL_ACCESS_KEY`, where the application finds it. Running it again
builds and deploys the current code as a new version of the same model.

Settings, all optional, from environment variables:

- `U4CAST_WORKBENCH_MODEL`: the name of the workbench model. Default `timestral`.
- `U4CAST_RUNTIME_IDENTIFIER`: the runtime image to build on. Default: the
  newest Python 3.12, Standard, PBJ Workbench runtime.
- `U4CAST_MODEL_CPU`, `U4CAST_MODEL_MEMORY`: vCPU and GB for a replica.
  Default 4 and 8.
- `U4CAST_MODEL_NAME`: the Timestral model to serve, passed on to the replica.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections.abc import Callable

import cmlapi

logger = logging.getLogger("u4cast.deploy_workbench_model")

MODEL_FILE = "serving/workbench_model.py"
MODEL_FUNCTION = "forecast"
ACCESS_KEY_VARIABLE = "U4CAST_MODEL_ACCESS_KEY"
POLL_SECONDS = 15
BUILD_TIMEOUT_SECONDS = 12 * 60
BUILD_ATTEMPTS = 3
DEPLOY_TIMEOUT_SECONDS = 20 * 60


def newest_runtime(client: cmlapi.CMLServiceApi) -> str:
    """Return the image of the newest Python 3.12, Standard, PBJ Workbench runtime."""
    search = json.dumps({"kernel": "Python 3.12", "edition": "Standard", "editor": "PBJ Workbench"})
    runtimes = client.list_runtimes(search_filter=search, page_size=500).runtimes
    if not runtimes:
        raise RuntimeError("This workbench has no Python 3.12, Standard, PBJ Workbench runtime.")
    return max(runtimes, key=lambda runtime: runtime.full_version).image_identifier


def find_or_create_model(
    client: cmlapi.CMLServiceApi, project_id: str, name: str, serves: str
) -> cmlapi.Model:
    search = json.dumps({"name": name})
    for model in client.list_models(project_id, search_filter=search, page_size=100).models:
        if model.name == name:
            logger.info("Using the existing workbench model %s", name)
            return model
    logger.info("Creating the workbench model %s", name)
    request = cmlapi.CreateModelRequest(
        project_id=project_id,
        name=name,
        description=(f"Timestral forecasts for u4cast. Serves {serves}."),
        disable_authentication=False,
    )
    return client.create_model(request, project_id)


def wait_for(
    what: str, read_status: Callable[[], str], done: str, failed: tuple[str, ...], timeout: int
) -> None:
    """Poll `read_status` until it returns `done`. Raise if it fails or takes too long."""
    started = time.monotonic()
    last = None
    while True:
        status = read_status()
        if status != last:
            logger.info("%s: %s (%.0f s)", what, status, time.monotonic() - started)
            last = status
        if status == done:
            return
        if status in failed:
            raise RuntimeError(f"{what} ended as {status}.")
        if time.monotonic() - started > timeout:
            raise TimeoutError(f"{what} was still {status} after {timeout} seconds.")
        time.sleep(POLL_SECONDS)


def save_access_key(client: cmlapi.CMLServiceApi, project_id: str, access_key: str) -> None:
    """Save the access key in the project's environment variables, keeping the others."""
    project = client.get_project(project_id)
    environment = json.loads(project.environment or "{}")
    environment[ACCESS_KEY_VARIABLE] = access_key
    client.update_project(cmlapi.Project(environment=json.dumps(environment)), project_id)
    logger.info("Saved the access key in the project as %s", ACCESS_KEY_VARIABLE)


def deploy(
    name: str, serves: str, cpu: float = 4, memory: float = 8, save_key: bool = True
) -> cmlapi.Model:
    """Create, build and deploy one workbench model that serves the Timestral model `serves`.

    `save_key` stores its access key in the project as the model the
    application uses by default. Further models are deployed without it; the
    application finds them by their description.
    """
    client = cmlapi.default_client()
    project_id = os.environ["CDSW_PROJECT_ID"]
    runtime = os.environ.get("U4CAST_RUNTIME_IDENTIFIER") or newest_runtime(client)

    model = find_or_create_model(client, project_id, name, serves)

    build = None
    for attempt in range(1, BUILD_ATTEMPTS + 1):
        logger.info("Building %s for %s on %s, attempt %d", MODEL_FILE, name, runtime, attempt)
        build = client.create_model_build(
            cmlapi.CreateModelBuildRequest(
                project_id=project_id,
                model_id=model.id,
                comment="Built by serving/deploy_workbench_model.py",
                file_path=MODEL_FILE,
                function_name=MODEL_FUNCTION,
                kernel="python3",
                runtime_identifier=runtime,
            ),
            project_id,
            model.id,
        )
        try:
            wait_for(
                "Model build",
                lambda build=build: client.get_model_build(project_id, model.id, build.id).status,
                done="built",
                failed=("build failed", "failed"),
                timeout=BUILD_TIMEOUT_SECONDS,
            )
            break
        except TimeoutError:
            # A build can hang while its image is uploaded ("pushing"). The image
            # is stored all the same, and a second build then finishes at once.
            if attempt == BUILD_ATTEMPTS:
                raise
            logger.info("The build hung. Starting another one.")

    deployment = client.create_model_deployment(
        cmlapi.CreateModelDeploymentRequest(
            project_id=project_id,
            model_id=model.id,
            build_id=build.id,
            cpu=cpu,
            memory=memory,
            replicas=1,
            # The replica loads this model. Weights that are not in the image
            # are downloaded when it starts.
            environment={"U4CAST_MODEL_NAME": serves},
        ),
        project_id,
        model.id,
        build.id,
    )
    wait_for(
        "Model deployment",
        lambda: client.get_model_deployment(project_id, model.id, build.id, deployment.id).status,
        done="deployed",
        failed=("failed", "stopped"),
        timeout=DEPLOY_TIMEOUT_SECONDS,
    )
    if save_key:
        save_access_key(client, project_id, model.access_key)
    logger.info("The workbench model %s is deployed and serves %s.", name, serves)
    return model


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)
    deploy(
        name=os.environ.get("U4CAST_WORKBENCH_MODEL", "timestral"),
        serves=os.environ.get("U4CAST_MODEL_NAME", "mistralai/Timestral-209M"),
        cpu=float(os.environ.get("U4CAST_MODEL_CPU", 4)),
        memory=float(os.environ.get("U4CAST_MODEL_MEMORY", 8)),
    )


if __name__ == "__main__":
    main()
