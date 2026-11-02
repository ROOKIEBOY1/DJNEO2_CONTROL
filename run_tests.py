"""Offline/local-loopback tests. --extras adds optional capability tests."""
import argparse
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'core'))
sys.path.insert(1, str(ROOT / 'extras'))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--extras', action='store_true', help='Also test video, voice and camera/media; needs extras dependencies')
    args = parser.parse_args()
    if args.extras and any(importlib.util.find_spec(name) is None for name in ('av', 'PIL')):
        parser.error('Install requirements-extras.txt before running --extras')
    suite = unittest.TestLoader().discover(str(ROOT / 'tests'), pattern='test_*.py')
    if args.extras:
        suite.addTests(unittest.TestLoader().discover(str(ROOT / 'tests_optional'), pattern='test_*.py'))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
