#!/bin/bash
# Cloudera AI runs this script when it builds the image of the workbench model
# that serves Timestral. The image starts from a copy of the project files
# that leaves out everything in .gitignore, so nothing installed in a session
# and no downloaded weights are in it. This script adds both.
set -euo pipefail

# torch, at the version in the lock file, from the CPU-only index. The default
# build bundles GPU libraries the model does not use.
TORCH_VERSION=$(python3 - <<'EOF'
import tomllib

with open("uv.lock", "rb") as lock_file:
    packages = tomllib.load(lock_file)["package"]
print(next(p["version"] for p in packages if p["name"] == "torch").split("+")[0])
EOF
)
pip3 install --no-cache-dir "torch==${TORCH_VERSION}" --index-url https://download.pytorch.org/whl/cpu

# This project with Timestral, at the commit pinned in pyproject.toml. numpy
# and pandas come from the runtime, which pyproject.toml's version ranges allow.
pip3 install --no-cache-dir ".[model]"

# The weights, so a replica starts without downloading anything. HF_TOKEN is
# used when it is set; the download does not need it.
python3 - <<'EOF'
from timestral.loading import resolve_checkpoint

from u4cast.settings import Settings

print("Downloaded", resolve_checkpoint(Settings.from_environment().model_name))
EOF
