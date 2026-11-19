"""Create and run the Cloudera AI job that deploys the smaller Timestral models.

Run inside Cloudera AI, in a session:

    python3 serving/more_models_job.py

The job is named "u4cast: deploy more models" and can be run again from the
Jobs page. What it deploys is described in `serving/deploy_more_models.py`.
"""

from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "amp"))

from steps import run_job

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)
    run_job("u4cast: deploy more models", "serving/deploy_more_models.py")
