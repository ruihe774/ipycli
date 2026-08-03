"""On-disk state: kernel registry, per-kernel history, and captured plots.

Every CLI invocation is a separate process, so all cross-invocation state lives
under a state directory (``~/.ipycli`` by default, overridable with
``IPYCLI_HOME``). Layout::

    <home>/kernels/<kernel_id>/
        connection.json   # jupyter connection file (ports + HMAC key)
        meta.json         # {kernel_id, spec, pid, started_at, connection_file, bootstrapped}
        history.json      # [ {block_id, code, status, outputs[], plots[], ts}, ... ]
        outputs/          # captured PNGs: <block_id>_<n>.png
        kernel.log        # kernel stdout/stderr
"""

import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Optional


class StateError(Exception):
    """Raised for unrecoverable state problems (missing/ambiguous kernel, etc.)."""


def home() -> Path:
    """Return the ipycli state directory, creating it if needed."""
    root = Path(os.environ.get("IPYCLI_HOME", Path.home() / ".ipycli"))
    (root / "kernels").mkdir(parents=True, exist_ok=True)
    return root


def kernels_dir() -> Path:
    return home() / "kernels"


def kernel_dir(kernel_id: str) -> Path:
    return kernels_dir() / kernel_id


def new_id() -> str:
    """Short unique id (8 hex chars) for kernels and code blocks."""
    return uuid.uuid4().hex[:8]


# --- low-level json helpers ------------------------------------------------


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


# --- kernel registry -------------------------------------------------------


def pid_alive(pid: int) -> bool:
    """Return True if a process with ``pid`` currently exists."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but owned by another user — still "alive".
        return True
    return True


def create_kernel_dir(kernel_id: str) -> Path:
    d = kernel_dir(kernel_id)
    (d / "outputs").mkdir(parents=True, exist_ok=True)
    return d


def save_meta(kernel_id: str, meta: dict[str, Any]) -> None:
    _write_json(kernel_dir(kernel_id) / "meta.json", meta)


def load_meta(kernel_id: str) -> Optional[dict[str, Any]]:
    path = kernel_dir(kernel_id) / "meta.json"
    if not path.exists():
        return None
    return _read_json(path, None)


def list_metas(only_alive: bool = True, prune: bool = True) -> list[dict[str, Any]]:
    """Return metadata for all registered kernels.

    When ``prune`` is set, registry entries whose process is dead are removed
    from disk. When ``only_alive`` is set, dead kernels are omitted from the
    returned list.
    """
    metas: list[dict[str, Any]] = []
    kdir = kernels_dir()
    if not kdir.exists():
        return metas
    for entry in sorted(kdir.iterdir()):
        if not entry.is_dir():
            continue
        meta = load_meta(entry.name)
        if meta is None:
            continue
        alive = pid_alive(int(meta.get("pid", -1)))
        meta = {**meta, "alive": alive}
        if not alive:
            if prune:
                remove_kernel(entry.name)
            if only_alive:
                continue
        metas.append(meta)
    return metas


def remove_kernel(kernel_id: str) -> None:
    """Delete a kernel's entire state directory."""
    d = kernel_dir(kernel_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def resolve_kernel_id(kernel_id: Optional[str]) -> str:
    """Resolve which kernel a command targets.

    Uses ``kernel_id`` if given (and it must be alive). Otherwise, if exactly
    one kernel is running, uses that. Errors on 0 or >1 with none specified.
    """
    if kernel_id:
        meta = load_meta(kernel_id)
        if meta is None:
            raise StateError(f"No such kernel: {kernel_id}")
        if not pid_alive(int(meta.get("pid", -1))):
            remove_kernel(kernel_id)
            raise StateError(f"Kernel {kernel_id} is not running")
        return kernel_id

    alive = list_metas(only_alive=True)
    if not alive:
        raise StateError("No running kernels. Launch one with 'ipycli launch-kernel'.")
    if len(alive) > 1:
        ids = ", ".join(m["kernel_id"] for m in alive)
        raise StateError(
            f"Multiple kernels running ({ids}); specify one with --kernel-id."
        )
    return alive[0]["kernel_id"]


# --- history ---------------------------------------------------------------


def history_path(kernel_id: str) -> Path:
    return kernel_dir(kernel_id) / "history.json"


def load_history(kernel_id: str) -> list[dict[str, Any]]:
    return _read_json(history_path(kernel_id), [])


def append_history(kernel_id: str, block: dict[str, Any]) -> None:
    history = load_history(kernel_id)
    history.append(block)
    _write_json(history_path(kernel_id), history)


def clear_history(kernel_id: str) -> None:
    _write_json(history_path(kernel_id), [])
    outputs = kernel_dir(kernel_id) / "outputs"
    if outputs.exists():
        for f in outputs.iterdir():
            try:
                f.unlink()
            except OSError:
                pass


# --- background jobs -------------------------------------------------------
#
# A ``execute-code --background`` invocation records a *job* here and returns
# immediately; a detached worker process carries out the execution and updates
# the job's status. ``poll-background`` reads these files to monitor and reap.


def jobs_dir(kernel_id: str) -> Path:
    return kernel_dir(kernel_id) / "jobs"


def job_path(kernel_id: str, job_id: str) -> Path:
    return jobs_dir(kernel_id) / f"{job_id}.json"


def save_job(kernel_id: str, job: dict[str, Any]) -> None:
    d = jobs_dir(kernel_id)
    d.mkdir(parents=True, exist_ok=True)
    _write_json(d / f"{job['job_id']}.json", job)


def load_job(kernel_id: str, job_id: str) -> Optional[dict[str, Any]]:
    path = job_path(kernel_id, job_id)
    if not path.exists():
        return None
    return _read_json(path, None)


def list_jobs(kernel_id: str) -> list[dict[str, Any]]:
    d = jobs_dir(kernel_id)
    if not d.exists():
        return []
    out: list[dict[str, Any]] = []
    for entry in sorted(d.glob("*.json")):
        job = _read_json(entry, None)
        if job is not None:
            out.append(job)
    return out


def remove_job(kernel_id: str, job_id: str) -> None:
    """Delete a job's record and worker log (reap it)."""
    d = jobs_dir(kernel_id)
    for name in (f"{job_id}.json", f"{job_id}.log"):
        try:
            (d / name).unlink()
        except OSError:
            pass


def running_jobs(kernel_id: str) -> list[dict[str, Any]]:
    """Return jobs that are still executing.

    A job counts as running while its status is ``"running"`` and its worker is
    either still starting up (pid not yet claimed) or alive. A worker that died
    without reporting completion is *not* counted, so it never blocks new work.
    """
    out: list[dict[str, Any]] = []
    for job in list_jobs(kernel_id):
        if job.get("status") != "running":
            continue
        pid = int(job.get("pid", 0) or 0)
        if pid <= 0 or pid_alive(pid):
            out.append(job)
    return out
