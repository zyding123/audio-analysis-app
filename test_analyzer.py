#!/usr/bin/env python3
"""Compatibility entry point for regression checks; fixtures live in temporary directories."""
from pathlib import Path
import sys
import unittest

if __name__ == '__main__':
    suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent / 'tests'))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
