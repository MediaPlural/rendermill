"""`python -m rendermill.manim_lane` entry point.

Equivalent to `rendermill manim`; both reach build.main().
"""
from __future__ import annotations

import sys

from .build import main

if __name__ == "__main__":
    sys.exit(main())
