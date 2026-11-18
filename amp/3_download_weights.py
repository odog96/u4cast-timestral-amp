"""AMP step 3: download the Timestral weights from Hugging Face.

This copy serves the application when it runs the model in its own process.
The workbench model downloads its own copy when its image is built.

The weights have their own license from Mistral. It is separate from this
project's license, and each user is responsible for checking and complying
with it.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.getcwd(), "amp"))

from steps import PYTHON, run

# An access token is optional. An empty one must not be sent as a token.
if not os.environ.get("HF_TOKEN"):
    os.environ.pop("HF_TOKEN", None)

run(
    f"{PYTHON} -c 'from timestral.loading import resolve_checkpoint; "
    "from u4cast.settings import Settings; "
    'print("Weights are in", resolve_checkpoint(Settings.from_environment().model_name))\''
)
