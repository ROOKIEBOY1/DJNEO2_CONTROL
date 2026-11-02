"""Run only offline/local-loopback core tests. Standard library only."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'core'))

if __name__ == '__main__':
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern='test_*.py')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
