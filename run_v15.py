"""Official SAM EDGE V15 launcher.

Loads the pandas datetime compatibility layer explicitly before importing the
V15 runner/core, then starts the normal 5-minute paper-forward loop.
"""

import sitecustomize  # noqa: F401
import runpy

runpy.run_path("main_paper_v15.py", run_name="__main__")
