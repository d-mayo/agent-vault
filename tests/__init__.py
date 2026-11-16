"""The test package. The full check, `python -m unittest discover -s tests -t .`, runs test classes
in parallel worker processes (tests/parallel.py); `AGENT_VAULT_TESTS_SERIAL=1` runs it in one process."""
import os
import sys
from pathlib import Path

from tests.parallel import discover_all

if sys.version_info < (3, 10):          # Path.write_text(newline=) is 3.10+: the tests use it for LF notes
    def _write_text_newline(self, data, encoding=None, errors=None, newline=None):
        with open(self, "w", encoding=encoding, errors=errors, newline=newline) as f:
            return f.write(data)

    Path.write_text = _write_text_newline


def load_tests(loader, standard_tests, pattern):
    return discover_all(loader, os.path.dirname(os.path.abspath(__file__)), pattern)
