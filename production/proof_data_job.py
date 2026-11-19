"""Cloudera AI runs this file as the job that adds a week to the growing table.

It hands over to the project's virtual environment. See `production/proof_data.py`.
"""

import subprocess

subprocess.run(
    ["/home/cdsw/.venv/bin/python", "production/proof_data.py"], cwd="/home/cdsw", check=True
)
