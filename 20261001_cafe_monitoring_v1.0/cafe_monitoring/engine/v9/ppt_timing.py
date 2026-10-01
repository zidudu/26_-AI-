"""Shared elapsed-time measurements for PowerPoint phases."""
from contextlib import contextmanager
from time import perf_counter

@contextmanager
def phase(report, name):
    started = perf_counter()
    try:
        yield
    finally:
        timings = report.setdefault("ppt_timings_seconds", {})
        timings[name] = timings.get(name, 0.0) + perf_counter() - started


