"""AMP step 6: create and start the application.

See `webapp/deploy_application.py`. The application's address is shown at the
end of this step's log and under Applications.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.getcwd(), "amp"))

from steps import run

run("python3 webapp/deploy_application.py")
