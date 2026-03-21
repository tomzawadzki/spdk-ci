"""Shared pytest fixtures for common module tests."""

import sys
import os

INFRA_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if INFRA_DIR not in sys.path:
    sys.path.insert(0, INFRA_DIR)
