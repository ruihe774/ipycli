"""Background execution worker.

``execute-code --background`` records a job and returns immediately; the actual
execution is carried out here, by a detached worker process spawned per job.

The worker connects to the *already running* kernel and submits each block as
an ordinary ``execute_request`` on the shell channel. That is deliberate: an
IPython kernel processes shell-channel requests strictly one at a time, so
routing background work through the same kernel is what guarantees that no two
blocks — foreground or background — ever run in parallel. We rely on the kernel
for that serialization rather than inventing our own locking. The worker drains
outputs, records history exactly as a foreground run would, and updates the job
file so ``poll-background`` can monitor and reap it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone

from . import kernel, state


def spawn_worker(kernel_id: str, job_id: str) -> int:
    """Spawn a detached worker process for ``job_id``; return its pid."""
    log_path = state.jobs_dir(kernel_id) / f"{job_id}.log"
    log = open(log_path, "wb")
    proc = subprocess.Popen(
        [sys.executable, "-m", "ipycli", "_worker", kernel_id, job_id],
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,  # detach: survives the launching CLI's exit
    )
    return proc.pid


def run_job(kernel_id: str, job_id: str) -> None:
    """Execute a recorded job's blocks on the kernel and update its status."""
    job = state.load_job(kernel_id, job_id)
    if job is None:
        return

    # Claim the job: record our pid so the launcher and poll-background can tell
    # the worker is alive.
    job["pid"] = os.getpid()
    job["status"] = "running"
    state.save_job(kernel_id, job)

    timeout = job.get("timeout")
    results: list[dict] = []
    status = "done"
    error = None
    client = None
    try:
        client = kernel.connect(kernel_id)
        for src, bid in zip(job["blocks_src"], job["block_ids"]):
            results.append(
                kernel.execute(
                    kernel_id, src, client=client, block_id=bid, timeout=timeout
                )
            )
    except kernel.KernelError as exc:
        status = "error"
        error = str(exc)
    except Exception as exc:  # never leave a job wedged in "running"
        status = "error"
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if client is not None:
            client.stop_channels()

    job["results"] = results
    job["status"] = status
    job["error"] = error
    job["finished_at"] = datetime.now(timezone.utc).isoformat()
    state.save_job(kernel_id, job)
