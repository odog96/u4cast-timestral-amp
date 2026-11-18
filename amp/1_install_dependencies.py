"""AMP step 1: install the Python packages into the virtual environment `.venv`.

The packages and their exact versions come from `uv.lock`. A virtual
environment keeps them apart from the packages Cloudera AI's runtime itself
runs on.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.getcwd(), "amp"))

from steps import run

run("pip install --quiet --user uv")
run("uv sync --frozen --all-extras --group frontend")
