"""Run agent computations in a child process that can be killed at a deadline.

Python cannot safely stop a function that is running in the same process, so every
computation that may hang (large workbooks, generated code, slow joins) runs in a
child process. The parent waits at most ``timeout`` seconds; on timeout or crash the
child is killed and the next task starts a fresh one. The child keeps a small
``cache`` dict between tasks so a business is loaded once per worker lifetime.

Task functions must be importable at module level (``spawn`` pickles them by
reference) and take ``cache`` as their first argument.
"""

import math
import multiprocessing as mp
import threading
import time
import weakref
from dataclasses import dataclass
from multiprocessing.connection import Connection
from typing import Any, Callable

# "spawn" behaves the same on Linux, macOS and Windows and never copies parent state.
_CONTEXT = mp.get_context("spawn")


@dataclass(frozen=True)
class WorkerResult:
    ok: bool
    value: Any = None
    error: str = ""
    timed_out: bool = False
    elapsed_seconds: float = 0.0
    timeout_seconds: float = 0.0

    @property
    def status(self) -> str:
        return "ok" if self.ok else "timeout" if self.timed_out else "error"

    def execution(self) -> dict[str, Any]:
        """Execution metadata for per-question evidence logs."""
        result = {"status": self.status, "elapsed_seconds": round(self.elapsed_seconds, 6),
                  "timeout_seconds": self.timeout_seconds}
        if self.error:
            result["error"] = self.error
        return result


def _serve(connection: Connection) -> None:
    cache: dict[str, Any] = {}
    while True:
        try:
            message = connection.recv()
        except (EOFError, OSError):
            return
        if message is None:
            return
        function, kwargs = message
        try:
            reply = (True, function(cache, **kwargs), "")
        except Exception as exc:  # reported to the parent; the worker stays usable
            reply = (False, None, f"{type(exc).__name__}: {exc}")
        try:
            connection.send(reply)
        except Exception as exc:  # e.g. an unpicklable return value
            connection.send((False, None, f"Unsendable result: {type(exc).__name__}: {exc}"))


def _terminate(process: Any, connection: Connection) -> None:
    try:
        connection.close()
    finally:
        if process.is_alive():
            process.kill()
        process.join(timeout=5)


class Worker:
    """One reusable child process; replaced after a timeout or crash."""

    def __init__(self) -> None:
        self._process: Any = None
        self._connection: Connection | None = None
        self._finalizer: weakref.finalize | None = None
        # Incremented every time a new child starts; its cache starts empty.
        self.generation = 0
        self._lifecycle = threading.Lock()

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.is_alive()

    def run(self, function: Callable[..., Any], timeout: float, **kwargs: Any) -> WorkerResult:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a positive number of seconds")
        started = time.perf_counter()
        cancelled = threading.Event()
        reply: list[tuple[bool, Any, str]] = []

        def exchange() -> None:
            # send() and recv() can block even when poll() succeeded (large payloads).
            # Keep all IPC and spawn under the parent's one wall-clock deadline.
            try:
                if not self.alive:
                    self._start(cancelled)
                connection = self._connection
                if cancelled.is_set() or connection is None:
                    return
                connection.send((function, kwargs))
                reply.append(connection.recv())
            except Exception as exc:
                reply.append((False, None, f"Worker exited or IPC failed: "
                              f"{type(exc).__name__}: {exc}"))

        thread = threading.Thread(target=exchange, daemon=True, name="datamate-ipc")
        thread.start()
        thread.join(max(0.0, timeout - (time.perf_counter() - started)))
        if thread.is_alive() or time.perf_counter() - started >= timeout:
            cancelled.set()
            self.stop()
            return WorkerResult(False, error=f"Timed out after {timeout:g}s", timed_out=True,
                                elapsed_seconds=time.perf_counter() - started,
                                timeout_seconds=timeout)
        if not reply:
            self.stop()
            return WorkerResult(False, error="Worker exited without a reply",
                                elapsed_seconds=time.perf_counter() - started,
                                timeout_seconds=timeout)
        ok, value, error = reply[0]
        if not ok and error.startswith("Worker exited or IPC failed"):
            self.stop()
        return WorkerResult(ok, value, error, False, time.perf_counter() - started, timeout)

    def stop(self) -> None:
        """Kill the child immediately; the next run starts a new one."""
        with self._lifecycle:
            finalizer = self._finalizer
            self._process = self._connection = self._finalizer = None
        if finalizer is not None:
            finalizer()

    def close(self) -> None:
        """Ask an idle child to exit, then make sure it is gone."""
        if self._connection is not None and self._process is not None:
            try:
                self._connection.send(None)
                self._process.join(timeout=2)
            except (OSError, ValueError):
                pass
        self.stop()

    def _start(self, cancelled: threading.Event) -> None:
        self.stop()
        parent, child = _CONTEXT.Pipe()
        process = _CONTEXT.Process(target=_serve, args=(child,), daemon=True,
                                   name="datamate-worker")
        process.start()
        child.close()
        with self._lifecycle:
            if not cancelled.is_set():
                self._process, self._connection = process, parent
                # Kill the child at GC/interpreter exit as well as on timeout.
                self._finalizer = weakref.finalize(self, _terminate, process, parent)
                self.generation += 1
                return
        # A slow spawn may finish after run() has already timed out.
        _terminate(process, parent)
