"""Cloudera AI runs this file as the job of a forecast in production.

A job is started by the runtime's own Python. This project's packages live in
the virtual environment `.venv`, so the file only hands over to it. Which
forecast to run is in the job's environment variables.
"""

import subprocess

subprocess.run(
    ["/home/cdsw/.venv/bin/python", "production/run_forecast.py"], cwd="/home/cdsw", check=True
)
