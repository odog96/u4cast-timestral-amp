"""What the AMP's install steps share: running a command, and running a job with Spark.

Cloudera AI runs each step of `.project-metadata.yaml` as a script in a
session, with the runtime's own Python. The project's packages live in the
virtual environment `.venv`, which the first step creates.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time

ROOT = "/home/cdsw"
PYTHON = f"{ROOT}/.venv/bin/python"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)
logger = logging.getLogger("u4cast.amp")


def run(command: str, cwd: str = ROOT) -> None:
    """Run a shell command, show it, and stop the step if it fails."""
    logger.info("$ %s", command)
    # The project's Node and the tools pip installs for the user are not on the default path.
    path = f"{ROOT}/.venv/bin:{ROOT}/.local/bin:{os.environ['PATH']}"
    subprocess.run(command, shell=True, check=True, cwd=cwd, env={**os.environ, "PATH": path})


def run_job_with_spark(name: str, script: str, cpu: float = 4, memory: float = 8) -> None:
    """Run a script as a Cloudera AI job that has Spark, and wait for it to finish.

    A session started by an AMP has no Spark add-on, and the add-on's name
    differs between workbenches. So the step creates a job through Cloudera
    AI's API, with the add-ons looked up, and runs that.
    """
    import cmlapi

    sys.path.insert(0, ROOT)
    from webapp.deploy_application import addons, newest_runtime

    client = cmlapi.default_client()
    project_id = os.environ["CDSW_PROJECT_ID"]
    jobs = client.list_jobs(project_id, page_size=200).jobs
    job = next((found for found in jobs if found.name == name), None)
    if job is None:
        job = client.create_job(
            cmlapi.CreateJobRequest(
                project_id=project_id,
                name=name,
                script=script,
                kernel="python3",
                cpu=cpu,
                memory=memory,
                runtime_identifier=newest_runtime(client),
                runtime_addon_identifiers=addons(client),
            ),
            project_id,
        )
    run_request = cmlapi.CreateJobRunRequest(project_id=project_id, job_id=job.id)
    job_run = client.create_job_run(run_request, project_id, job.id)
    started, last = time.monotonic(), None
    while True:
        status = client.get_job_run(project_id, job.id, job_run.id).status
        if status != last:
            logger.info("Job %s: %s (%.0f s)", name, status, time.monotonic() - started)
            last = status
        if status == "ENGINE_SUCCEEDED":
            return
        if status in ("ENGINE_FAILED", "ENGINE_STOPPED", "ENGINE_TIMEDOUT"):
            raise RuntimeError(f"The job {name} ended as {status}. Its log is under Jobs.")
        time.sleep(10)
