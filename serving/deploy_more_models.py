"""Deploy further sizes of Timestral as workbench models, next to the default one.

Run inside Cloudera AI, in a session or as a job:

    python3 serving/deploy_more_models.py

Each model gets its own workbench model, named after its size, for example
`timestral-28m`. The application lists every workbench model of the project
that serves forecasts for it, so these appear in the "Model" choice of the
new forecast screen without a restart. This is not an install step: the
default model is enough to use the application.

Settings, from environment variables:

- `U4CAST_MORE_MODELS`: the Timestral models to deploy, by their Hugging Face
  names, separated by commas. Default: the 28M and the 123M model. Mistral
  also publishes `mistralai/Timestral-5M`.
- `U4CAST_MORE_MODELS_CPU`, `U4CAST_MORE_MODELS_MEMORY`: vCPU and GB for each
  replica. Default 2 and 4: these models are smaller than the default one.
"""

from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from deploy_workbench_model import deploy

logger = logging.getLogger("u4cast.deploy_more_models")

DEFAULT = "mistralai/Timestral-28M,mistralai/Timestral-123M"


def workbench_name(model_name: str) -> str:
    """Name the workbench model after the model it serves: timestral-28m."""
    return model_name.split("/")[-1].lower()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)
    names = [n.strip() for n in os.environ.get("U4CAST_MORE_MODELS", DEFAULT).split(",")]
    cpu = float(os.environ.get("U4CAST_MORE_MODELS_CPU", 2))
    memory = float(os.environ.get("U4CAST_MORE_MODELS_MEMORY", 4))
    for model_name in filter(None, names):
        deploy(workbench_name(model_name), model_name, cpu=cpu, memory=memory, save_key=False)


if __name__ == "__main__":
    main()
