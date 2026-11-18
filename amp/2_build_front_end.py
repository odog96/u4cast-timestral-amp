"""AMP step 2: build the React front end into `frontend/dist`.

The runtime has no Node. Step 1 installed it into `.venv` from the Python
package `nodejs-wheel`.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.getcwd(), "amp"))

from steps import ROOT, run

run("npm ci --no-audit --no-fund", cwd=f"{ROOT}/frontend")
run("npm run build", cwd=f"{ROOT}/frontend")
