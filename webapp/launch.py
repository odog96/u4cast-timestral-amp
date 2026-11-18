"""Cloudera AI runs this file to start the application.

A Cloudera AI application is a script started by the runtime's own Python.
This project's packages live in the virtual environment `.venv`, so the
script only hands over to it. The web server itself is `python -m webapp`.
"""

import subprocess

subprocess.run(["/home/cdsw/.venv/bin/python", "-m", "webapp"], cwd="/home/cdsw", check=True)
