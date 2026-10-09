"""The parallel runner (tests/parallel.py), run on throwaway test packages in subprocesses."""
import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

from tests.helpers import REPO, tmpdir
from tests.parallel import SERIAL, WORKERS, worker_count

INIT = """
import os
from tests.parallel import discover_all


def load_tests(loader, standard_tests, pattern):
    return discover_all(loader, os.path.dirname(os.path.abspath(__file__)), pattern)
"""

MIXED = """
import unittest


class A(unittest.TestCase):
    def test_passes(self):
        pass

    def test_fails(self):
        self.assertEqual(1, 2, "the failing message")

    def test_errors(self):
        raise ValueError("the error message")


class B(unittest.TestCase):
    @unittest.skip("not today")
    def test_skipped(self):
        pass

    @unittest.expectedFailure
    def test_expected_failure(self):
        self.assertTrue(False)
"""

PIDS = """
import os
import time
import unittest
from pathlib import Path


def meet(me, other):
    d = Path(os.environ["PIDDIR"])
    (d / me).write_text(str(os.getpid()))
    deadline = time.time() + 30
    while os.environ.get("PIDS_WAIT") and not (d / other).exists() and time.time() < deadline:
        time.sleep(0.05)


class A(unittest.TestCase):
    def test_a(self):
        meet("a", "b")


class B(unittest.TestCase):
    def test_b(self):
        meet("b", "a")
"""

DIES = """
import os
import unittest


class Killer(unittest.TestCase):
    def test_dies(self):
        os._exit(3)


class Fine(unittest.TestCase):
    def test_one(self):
        pass

    def test_two(self):
        pass

    def test_three(self):
        pass
"""


class RunnerCase(unittest.TestCase):
    def setUp(self):
        self._t = tmpdir()
        self.addCleanup(self._t.cleanup)
        self.root = Path(self._t.name)

    def package(self, body, name="throwaway"):
        pkg = self.root / name
        pkg.mkdir()
        (pkg / "__init__.py").write_text(textwrap.dedent(INIT), encoding="utf-8")
        (pkg / "test_things.py").write_text(textwrap.dedent(body), encoding="utf-8")
        return pkg

    def run_pkg(self, pkg, **env):
        base = {k: v for k, v in os.environ.items() if k not in (SERIAL, WORKERS)}
        base.update({"PYTHONPATH": str(REPO), **env})
        proc = subprocess.Popen([sys.executable, "-m", "unittest", "discover", "-s", str(pkg), "-t", str(self.root)],
                                cwd=self.root, env=base, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True)
        out, _ = proc.communicate(timeout=120)
        return proc, out


class WorkerCountTest(unittest.TestCase):  # T4 -> AC1
    def test_the_cpu_count_capped_by_the_classes(self):
        self.assertEqual(worker_count(5, {}, cpus=3), 3)
        self.assertEqual(worker_count(2, {}, cpus=8), 2)
        self.assertEqual(worker_count(0, {}, cpus=8), 1)

    def test_the_override_is_capped_by_the_classes_too(self):
        self.assertEqual(worker_count(5, {WORKERS: "2"}, cpus=8), 2)
        self.assertEqual(worker_count(5, {WORKERS: "12"}, cpus=8), 5)
        self.assertEqual(worker_count(5, {WORKERS: "nope"}, cpus=3), 3)


class ParallelRunTest(RunnerCase):
    def test_counts_and_tracebacks_match_a_serial_run(self):          # T3 -> AC1, T5 -> AC2
        pkg = self.package(MIXED)
        for env in ({WORKERS: "2"}, {SERIAL: "1"}):
            with self.subTest(env=env):
                proc, out = self.run_pkg(pkg, **env)
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn("Ran 5 tests", out)
                for word in ("failures=1", "errors=1", "skipped=1", "expected failures=1"):
                    self.assertIn(word, out)
                self.assertIn("FAIL: test_fails (throwaway.test_things.A.test_fails)", out)
                self.assertIn("ERROR: test_errors (throwaway.test_things.A.test_errors)", out)
                self.assertIn("Traceback", out)
                self.assertIn('self.assertEqual(1, 2, "the failing message")', out)
                self.assertIn("1 != 2 : the failing message", out)
                self.assertIn('raise ValueError("the error message")', out)
                self.assertIn("ValueError: the error message", out)

    def test_classes_run_in_different_worker_processes(self):         # T4 -> AC1
        pkg = self.package(PIDS)
        piddir = self.root / "pids"
        piddir.mkdir()
        proc, out = self.run_pkg(pkg, **{WORKERS: "2", "PIDDIR": str(piddir), "PIDS_WAIT": "1"})
        self.assertEqual(proc.returncode, 0, out)
        a, b = (int((piddir / n).read_text()) for n in "ab")
        self.assertNotEqual(a, b)
        self.assertNotIn(proc.pid, (a, b))

    def test_serial_runs_every_test_in_the_parent_process(self):      # T5 -> AC2
        pkg = self.package(PIDS)
        piddir = self.root / "pids"
        piddir.mkdir()
        proc, out = self.run_pkg(pkg, **{SERIAL: "1", "PIDDIR": str(piddir)})
        self.assertEqual(proc.returncode, 0, out)
        self.assertEqual({int((piddir / n).read_text()) for n in "ab"}, {proc.pid})

    def test_a_dying_worker_fails_the_run_and_names_the_lost_test(self):    # T8 -> AC1
        pkg = self.package(DIES)
        proc, out = self.run_pkg(pkg, **{WORKERS: "2"})
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("throwaway.test_things.Killer.test_dies", out)
        self.assertIn("exited with status 3", out)
        self.assertIn("Ran 4 tests", out)
        self.assertIn("errors=1", out)
