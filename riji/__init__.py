"""
Riji: Desktop colocalization and live-cell image analysis for microscopy.

Author: Rchin Bari
"""

import os
import sys

# Ensure package root is in sys.path so submodules can import coloc and cell_viability
_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
if _PACKAGE_DIR not in sys.path:
    sys.path.insert(0, _PACKAGE_DIR)

__version__ = "1.0.0"
__author__ = "Rchin Bari"

__all__ = ["coloc", "coloc_gui", "coloc_outputs", "cell_viability"]
