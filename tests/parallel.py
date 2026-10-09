"""Runs the discovered tests in parallel worker processes, behind the one full-check command.

`python -m unittest discover -s tests -t .` finds `load_tests` in `tests/__init__.py`, which calls
`discover_all` here. Unless `AGENT_VAULT_TESTS_SERIAL=1`, the tests it finds come back as a
`ParallelSuite`: running it hands the tests, grouped by class (a long class in several chunks), to
worker processes and replays each worker's outcomes into the parent's result, so the summary, the
tracebacks and the exit code are the ones a serial run would give. A worker that dies has the tests
it had not reported counted as errors.

Worker protocol: the parent writes one JSON list of test ids per line to the worker's stdin; the
worker answers with JSON event lines on a private copy of its stdout and ends each batch with
`done`. Tests must not share any path or state outside their own temp folder, because classes run
at the same time."""
from __future__ import annotations

import collections
import json
import os
import queue
import subprocess
import sys
import threading
import time
import unittest

SERIAL = "AGENT_VAULT_TESTS_SERIAL"
WORKERS = "AGENT_VAULT_TESTS_WORKERS"
CHUNK = 10           # a class with more tests than this is split into chunks of about this size
FIRST_MODULE = "test_github"     # most of the time is here, so its units go first


class WorkerTraceback(Exception):
    """Carries a worker's formatted traceback text into the parent's result."""

    def __str__(self):
        return self.args[0]


WorkerTraceback.__module__ = "builtins"


def worker_count(classes: int, environ=None, cpus: int | None = None) -> int:
    """`min(cpu count, classes)`, or the override when `AGENT_VAULT_TESTS_WORKERS` is set (still capped
    by the number of classes); never less than one."""
    environ = os.environ if environ is None else environ
    cpus = cpus or os.cpu_count() or 1
    override = environ.get(WORKERS, "").strip()
    wanted = int(override) if override.isdigit() else cpus
    return max(1, min(wanted, classes))


def discover_all(loader, package_dir, pattern):
    """The `load_tests` body: discover the whole package, then wrap it unless serial."""
    top = getattr(loader, "_top_level_dir", None) or os.path.dirname(package_dir)
    suite = loader.discover(start_dir=package_dir, pattern=pattern, top_level_dir=top)
    if os.environ.get(SERIAL) == "1":
        return suite
    return ParallelSuite(suite, top)


def _flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten(item)
        else:
            yield item


def _units(tests):
    """[(class name, [test ids])] in dispatch order: the first module's, then the biggest classes."""
    groups = collections.OrderedDict()
    for t in tests:
        groups.setdefault(type(t), []).append(t.id())
    units = []
    for cls, ids in groups.items():
        parts = max(1, -(-len(ids) // CHUNK))
        size = -(-len(ids) // parts)
        for i in range(0, len(ids), size):
            units.append((cls, ids[i:i + size]))
    units.sort(key=lambda u: (u[0].__module__.rpartition(".")[2] != FIRST_MODULE, -len(u[1])))
    return [(f"{c.__module__}.{c.__qualname__}", ids) for c, ids in units]


class ParallelSuite(unittest.TestSuite):
    def __init__(self, suite, top_level_dir):
        super().__init__([suite])
        self._top = str(top_level_dir)

    def run(self, result, debug=False):
        tests = {}
        local = []
        for t in _flatten(self):
            if type(t).__module__.startswith("unittest."):      # an import failure: nothing to hand over
                local.append(t)
            else:
                tests[t.id()] = t
        for t in local:
            t(result)
        units = collections.deque(_units(tests.values()))
        if units and not result.shouldStop:
            _Pool(tests, units, worker_count(len({type(t) for t in tests.values()})), self._top).run(result)
        return result


class _Worker:
    def __init__(self, slot, events, top):
        self.slot = slot
        env = {**os.environ, SERIAL: "1"}
        env.pop(WORKERS, None)
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = ("import sys; sys.path[:0] = sys.argv[1:3]; from tests.parallel import worker_main; worker_main()")
        self.proc = subprocess.Popen([sys.executable, "-c", code, repo, top], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, env=env, text=True, encoding="utf-8")
        self.unit = None            # (name, ids) being run
        self.reported = set()
        threading.Thread(target=self._read, args=(events,), daemon=True).start()

    def _read(self, events):
        for line in self.proc.stdout:
            try:
                events.put((self, json.loads(line)))
            except ValueError:
                continue
        self.proc.stdout.close()
        events.put((self, None))

    def send(self, unit):
        self.unit = unit
        self.reported = set()
        try:
            self.proc.stdin.write(json.dumps(unit[1]) + "\n")
            self.proc.stdin.flush()
            return True
        except OSError:
            return False

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()
        try:
            self.proc.stdin.close()
        except OSError:
            pass


class _Pool:
    def __init__(self, tests, units, count, top):
        self.tests, self.units, self.count, self.top = tests, units, count, top
        self.events = queue.Queue()
        self.started = set()

    def run(self, result):
        workers = {}
        try:
            for slot in range(self.count):
                workers[slot] = _Worker(slot, self.events, self.top)
            for w in list(workers.values()):
                self._feed(w)
            busy = sum(1 for w in workers.values() if w.unit)
            while busy:
                w, ev = self.events.get()
                if w is not workers.get(w.slot):          # a replaced worker's late end of output
                    continue
                if ev is None:
                    self._died(w, result)
                    if self.units and not result.shouldStop:
                        w = workers[w.slot] = _Worker(w.slot, self.events, self.top)
                        self._feed(w)
                    busy = sum(1 for x in workers.values() if x.unit)
                    continue
                if ev["e"] == "done":
                    w.unit = None
                    if self.units and not result.shouldStop:
                        self._feed(w)
                else:
                    self._apply(w, ev, result)
                busy = sum(1 for x in workers.values() if x.unit)
        finally:
            for w in workers.values():
                w.kill() if result.shouldStop or w.unit else w.close()

    def _feed(self, w):
        unit = self.units.popleft()
        if not w.send(unit):
            self.units.appendleft(unit)

    def _apply(self, w, ev, result):
        test = self.tests[ev["id"]]
        kind = ev["e"]
        if kind == "start":
            result.startTest(test)
            self.started.add(ev["id"])
            return
        err = (WorkerTraceback, WorkerTraceback(ev.get("tb", "")), None)
        if kind == "ok":
            result.addSuccess(test)
        elif kind == "fail":
            result.addFailure(test, err)
        elif kind == "error":
            result.addError(test, err)
        elif kind == "skip":
            result.addSkip(test, ev["reason"])
        elif kind == "xfail":
            result.addExpectedFailure(test, err)
        elif kind == "xpass":
            result.addUnexpectedSuccess(test)
        elif kind == "stop":
            if hasattr(result, "addDuration"):
                result.addDuration(test, ev["t"])
            result.stopTest(test)
            self.started.discard(ev["id"])
            w.reported.add(ev["id"])
            if result.failfast and (result.failures or result.errors):
                result.stop()

    def _died(self, w, result):
        if not w.unit:
            w.kill()
            return
        w.kill()
        code = w.proc.returncode
        for tid in w.unit[1]:
            if tid in w.reported:
                continue
            test = self.tests[tid]
            if tid not in self.started:
                result.startTest(test)
            self.started.discard(tid)
            msg = (f"worker process exited with status {code} before it reported {tid}\n"
                   f"(unit {w.unit[0]})")
            result.addError(test, (WorkerTraceback, WorkerTraceback(msg), None))
            result.stopTest(test)
        w.unit = None


# --- the worker side ---

class _EventResult(unittest.TestResult):
    def __init__(self, out):
        super().__init__()
        self.out = out
        self.t0 = {}

    def emit(self, **ev):
        self.out.write(json.dumps(ev) + "\n")
        self.out.flush()

    def startTest(self, test):
        super().startTest(test)
        self.t0[test.id()] = time.perf_counter()
        self.emit(e="start", id=test.id())

    def stopTest(self, test):
        super().stopTest(test)
        self.emit(e="stop", id=test.id(), t=time.perf_counter() - self.t0.pop(test.id(), time.perf_counter()))

    def _tb(self, err, test):
        return self._exc_info_to_string(err, test)

    def addSuccess(self, test):
        self.emit(e="ok", id=test.id())

    def addFailure(self, test, err):
        self.emit(e="fail", id=test.id(), tb=self._tb(err, test))

    def addError(self, test, err):
        self.emit(e="error", id=test.id(), tb=self._tb(err, test))

    def addSubTest(self, test, subtest, err):
        if err is not None:
            kind = "fail" if issubclass(err[0], test.failureException) else "error"
            self.emit(e=kind, id=test.id(), tb=f"[{subtest._subDescription()}]\n" + self._tb(err, subtest))

    def addSkip(self, test, reason):
        self.emit(e="skip", id=test.id(), reason=reason)

    def addExpectedFailure(self, test, err):
        self.emit(e="xfail", id=test.id(), tb=self._tb(err, test))

    def addUnexpectedSuccess(self, test):
        self.emit(e="xpass", id=test.id())


def worker_main():
    """Read batches of test ids from stdin, run each batch, report events on the original stdout."""
    proto = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)                    # anything a test prints to stdout goes to stderr instead
    sys.stdin.reconfigure(encoding="utf-8")
    result = _EventResult(proto)
    loader = unittest.TestLoader()
    for line in sys.stdin:
        ids = json.loads(line)
        unittest.TestSuite(loader.loadTestsFromNames(ids)).run(result)
        result.emit(e="done")
