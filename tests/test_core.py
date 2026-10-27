"""Deterministic self-tests for SigmaOS-website (FIRST principles: no external I/O)."""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import core

class TestSelf(unittest.TestCase):
    def test_self_test_passes(self):
        core.self_test()

if __name__ == "__main__":
    unittest.main()
