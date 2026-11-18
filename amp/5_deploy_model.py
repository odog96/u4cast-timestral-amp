"""AMP step 5: create, build and deploy the workbench model that serves Timestral.

See `serving/deploy_workbench_model.py`. Building the model image takes a few
minutes: it installs torch and downloads the weights into the image.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.getcwd(), "amp"))

from steps import run

run("python3 serving/deploy_workbench_model.py")
