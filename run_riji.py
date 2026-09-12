#!/usr/bin/env python3
"""
Riji - Desktop colocalization and live-cell analysis
Root launcher script.

Usage:
    python run_riji.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RIJI_DIR = os.path.join(HERE, "riji")
if RIJI_DIR not in sys.path:
    sys.path.insert(0, RIJI_DIR)

from coloc_gui import main

if __name__ == "__main__":
    main()
