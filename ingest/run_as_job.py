"""Cloudera AI runs this file as the job that ingests the demo data.

A job is started by the runtime's own Python. This project's packages live in
the virtual environment `.venv`, so the file only hands over to it.
"""

import subprocess

subprocess.run(
    ["/home/cdsw/.venv/bin/python", "ingest/ingest_iowa.py"], cwd="/home/cdsw", check=True
)
