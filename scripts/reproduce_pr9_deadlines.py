"""Read-only control-flow probes for AGENT-01; no competition data required.

Run with Python from a prepared environment, passing the PR checkout. These
probes mock IPC latency/state; they do not benchmark real process startup.
Exit 0 means probes executed; inspect reproduced=true to identify each defect.
"""

import argparse
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkout", required=True, type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.checkout.resolve() / "src"))
    from datamate.agent.worker import Worker, WorkerResult

    from datamate.agent.baseline import BaselineAgent

    agent = BaselineAgent()
    agent.worker.generation = 1
    context = SimpleNamespace(cache={"worker_sales": {"ok": True, "generation": 1}}, catalog=None,
                              split=SimpleNamespace(rulebook=""))
    agent.worker.stop()
    calls = []

    def tracked_run(*args, **kwargs):
        calls.append(args[1])
        return WorkerResult(True, value={"ok": True})

    agent.worker.run = tracked_run
    agent._prepare(context)
    results = [
        {
            "case": "prepare_after_worker_stop",
            "reproduced": not calls,
            "ingestion_calls": calls,
            "expected": "Reload with ingestion_timeout before computing next question.",
        }
    ]

    class Process:
        def is_alive(self):
            return True

    class Connection:
        def send(self, message):
            time.sleep(0.1)

        def poll(self, timeout):
            return True

        def recv(self):
            return True, 42, ""

    worker = Worker()
    worker._process, worker._connection = Process(), Connection()
    try:
        result = worker.run(None, 0.01)
        results.append(
            {
                "case": "slow_ipc_send",
                "reproduced": result.ok and result.elapsed_seconds > 0.01,
                "status": result.status,
                "elapsed_seconds": result.elapsed_seconds,
                "timeout_seconds": 0.01,
                "expected": "Task exceeding total deadline reports timeout.",
            }
        )
    finally:
        worker._process = worker._connection = None
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
