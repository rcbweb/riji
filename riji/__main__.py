"""
Main entrypoint for running Riji as a module: `python -m riji`
"""

import os
import sys

_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
if _PACKAGE_DIR not in sys.path:
    sys.path.insert(0, _PACKAGE_DIR)

from coloc_gui import main

if __name__ == "__main__":
    main()
