"""The test package. The full check, `python -m unittest discover -s tests -t .`, runs test classes
in parallel worker processes (tests/parallel.py); `AGENT_VAULT_TESTS_SERIAL=1` runs it in one process."""
import os

from tests.parallel import discover_all


def load_tests(loader, standard_tests, pattern):
    return discover_all(loader, os.path.dirname(os.path.abspath(__file__)), pattern)
