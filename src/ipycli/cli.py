"""ipycli command-line interface.

A CLI for LLM coding agents to execute code with IPython kernels. Every command
defaults to JSON output (for reliable machine parsing) and accepts ``--plain``
for human-readable text.
"""

import json
import sys
import time
from typing import Optional

import typer

from . import kernel, notebook, output, state, worker

app = typer.Typer(
    help="Execute code with IPython kernels — for LLM coding agents.",
    no_args_is_help=True,
    add_completion=False,
)

PlainOpt = typer.Option(False, "--plain", help="Human-readable output instead of JSON.")
KernelIdOpt = typer.Option(
    None, "--kernel-id", "-k",
    help="Target kernel id. Defaults to the sole running kernel if only one.",
)


def _fail(message: str, plain: bool) -> None:
    """Emit an error and exit non-zero."""
    if plain:
        print(f"error: {message}", file=sys.stderr)
    else:
        print(json.dumps({"error": message}, indent=2), file=sys.stderr)
    raise typer.Exit(code=1)


@app.command("list-kernelspecs")
def list_kernelspecs(plain: bool = PlainOpt):
    """List the installed kernel specs."""
    output.emit(kernel.list_kernelspecs(), plain, output.kernelspecs)


@app.command("launch-kernel")
def launch_kernel(
    spec: str = typer.Argument("python3", help="Kernel spec name (see list-kernelspecs)."),
    cwd: Optional[str] = typer.Option(
        None, "--cwd", help="Working directory for the kernel process. Defaults to the current directory.",
    ),
    plain: bool = PlainOpt,
):
    """Launch a kernel in the background (detached) and print its unique id."""
    try:
        kernel_id = kernel.launch(spec, cwd=cwd)
    except kernel.KernelError as exc:
        _fail(str(exc), plain)
    meta = state.load_meta(kernel_id)
    output.emit(meta, plain, output.launched)


@app.command("list-kernels")
def list_kernels(plain: bool = PlainOpt):
    """List running kernels."""
    output.emit(state.list_metas(only_alive=True), plain, output.kernels)


@app.command("execute-code")
def execute_code(
    code: Optional[str] = typer.Option(None, "-c", "--code", help="Code to execute."),
    file: Optional[str] = typer.Argument(None, help="Path to a .py or .ipynb file."),
    kernel_id: Optional[str] = KernelIdOpt,
    timeout: Optional[float] = typer.Option(
        None, "--timeout", "-t",
        help="Max seconds a single block may run before its status becomes "
        "'timeout'. Default 300 (foreground) or 3600 (--background).",
    ),
    background: bool = typer.Option(
        False, "--background", "-b",
        help="Run detached in the background and return a job id immediately; "
        "monitor and reap it with 'poll-background'.",
    ),
    plain: bool = PlainOpt,
):
    """Execute code from -c, a file, or stdin. Prints block id(s), outputs, plot paths."""
    try:
        kid = state.resolve_kernel_id(kernel_id)
    except state.StateError as exc:
        _fail(str(exc), plain)

    try:
        blocks_src = notebook.read_blocks(code, file)
    except (FileNotFoundError, OSError) as exc:
        _fail(str(exc), plain)

    if not blocks_src:
        _fail("No code to execute (empty input).", plain)

    # No parallel executions: an IPython kernel runs shell requests serially, so
    # a second invocation would silently queue behind an in-flight background
    # job (and race it on history writes). Refuse instead, with a clear pointer.
    running = state.running_jobs(kid)
    if running:
        _fail(
            f"A background execution is already in progress on kernel {kid} "
            f"(job {running[0]['job_id']}). Wait for it to finish and reap it "
            "with 'ipycli poll-background' before running more code.",
            plain,
        )

    if background:
        _launch_background(kid, blocks_src, timeout, plain)
        return

    client = kernel.connect(kid)
    results = []
    try:
        for src in blocks_src:
            results.append(kernel.execute(kid, src, client=client, timeout=timeout))
    except kernel.KernelError as exc:
        _fail(str(exc), plain)
    finally:
        client.stop_channels()

    payload = results[0] if len(results) == 1 else {"kernel_id": kid, "blocks": results}
    output.emit(payload, plain, output.execution)


def _launch_background(
    kid: str, blocks_src: list[str], timeout: Optional[float], plain: bool
) -> None:
    """Record a background job and spawn its detached worker."""
    eff_timeout = kernel.BG_EXEC_TIMEOUT if timeout is None else timeout
    block_ids = [state.new_id() for _ in blocks_src]
    job_id = state.new_id()
    job = {
        "job_id": job_id,
        "kernel_id": kid,
        "pid": 0,
        "status": "running",
        "block_ids": block_ids,
        "blocks_src": blocks_src,
        "timeout": eff_timeout,
        "started_at": kernel._now(),
        "finished_at": None,
        "results": None,
        "error": None,
    }
    state.save_job(kid, job)

    try:
        pid = worker.spawn_worker(kid, job_id)
    except OSError as exc:
        state.remove_job(kid, job_id)
        _fail(f"Failed to start background worker: {exc}", plain)

    # Record the worker's pid only if it hasn't claimed the job itself yet, so
    # we never clobber a status the worker may already have written.
    fresh = state.load_job(kid, job_id)
    if fresh and fresh.get("status") == "running" and int(fresh.get("pid", 0) or 0) == 0:
        fresh["pid"] = pid
        state.save_job(kid, fresh)

    output.emit(
        {
            "kernel_id": kid,
            "job_id": job_id,
            "status": "running",
            "block_ids": block_ids,
            "timeout": eff_timeout,
            "message": (
                f"Started background job {job_id} ({len(block_ids)} block(s)). "
                f"Monitor with 'ipycli poll-background --job-id {job_id}'."
            ),
        },
        plain,
        output.message,
    )


@app.command("poll-background")
def poll_background(
    kernel_id: Optional[str] = KernelIdOpt,
    job_id: Optional[str] = typer.Option(
        None, "--job-id", "-j", help="Only report/reap this job."
    ),
    wait: bool = typer.Option(
        False, "--wait", help="Block until the targeted job(s) finish before reporting."
    ),
    timeout: Optional[float] = typer.Option(
        None, "--timeout", "-t", help="With --wait, stop blocking after this many seconds."
    ),
    keep: bool = typer.Option(
        False, "--keep", help="Report finished jobs but do not reap (delete) them."
    ),
    plain: bool = PlainOpt,
):
    """Monitor and reap background executions started with 'execute-code --background'.

    Reports each job's status; finished jobs include their block results and are
    reaped (removed) unless --keep is given.
    """
    try:
        kid = state.resolve_kernel_id(kernel_id)
    except state.StateError as exc:
        _fail(str(exc), plain)

    if job_id is not None and state.load_job(kid, job_id) is None:
        _fail(f"No such background job: {job_id}", plain)

    def snapshot() -> list[dict]:
        jobs = state.list_jobs(kid)
        if job_id is not None:
            jobs = [j for j in jobs if j.get("job_id") == job_id]
        return [_reconcile(j) for j in jobs]

    if wait:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            jobs = snapshot()
            if not jobs or all(j["status"] != "running" for j in jobs):
                break
            if deadline is not None and time.monotonic() >= deadline:
                break
            time.sleep(0.5)

    reported = []
    for j in snapshot():
        entry = dict(j)
        if j["status"] != "running" and not keep:
            state.remove_job(kid, j["job_id"])
            entry["reaped"] = True
        else:
            entry["reaped"] = False
        reported.append(entry)

    output.emit({"kernel_id": kid, "jobs": reported}, plain, output.jobs)


def _reconcile(job: dict) -> dict:
    """Flag a job whose worker died without reporting completion as failed."""
    if job.get("status") == "running":
        pid = int(job.get("pid", 0) or 0)
        if pid > 0 and not state.pid_alive(pid):
            return {
                **job,
                "status": "failed",
                "error": job.get("error")
                or "background worker process exited before finishing",
            }
    return job


@app.command("_worker", hidden=True)
def _worker_cmd(
    kernel_id: str = typer.Argument(...),
    job_id: str = typer.Argument(...),
):
    """Internal: run a background job's blocks (spawned detached by --background)."""
    worker.run_job(kernel_id, job_id)


@app.command("list-variables")
def list_variables(kernel_id: Optional[str] = KernelIdOpt, plain: bool = PlainOpt):
    """List variables defined in the kernel."""
    try:
        kid = state.resolve_kernel_id(kernel_id)
    except state.StateError as exc:
        _fail(str(exc), plain)
    try:
        variables = kernel.list_variables(kid)
    except kernel.KernelError as exc:
        _fail(str(exc), plain)
    output.emit(variables, plain, output.variables)


@app.command("restart-kernel")
def restart_kernel(kernel_id: Optional[str] = KernelIdOpt, plain: bool = PlainOpt):
    """Restart the kernel and clear its history."""
    try:
        kid = state.resolve_kernel_id(kernel_id)
        kernel.restart(kid)
    except (state.StateError, kernel.KernelError) as exc:
        _fail(str(exc), plain)
    output.emit(
        {"kernel_id": kid, "message": f"Restarted kernel {kid} and cleared history."},
        plain,
        output.message,
    )


@app.command("stop-kernel")
def stop_kernel(kernel_id: Optional[str] = KernelIdOpt, plain: bool = PlainOpt):
    """Stop the kernel."""
    try:
        kid = state.resolve_kernel_id(kernel_id)
        kernel.stop(kid)
    except (state.StateError, kernel.KernelError) as exc:
        _fail(str(exc), plain)
    output.emit(
        {"kernel_id": kid, "message": f"Stopped kernel {kid}."}, plain, output.message
    )


@app.command("show-history")
def show_history(kernel_id: Optional[str] = KernelIdOpt, plain: bool = PlainOpt):
    """Show the code blocks that have been run with their ids and outputs."""
    try:
        kid = state.resolve_kernel_id(kernel_id)
    except state.StateError as exc:
        _fail(str(exc), plain)
    blocks = state.load_history(kid)
    output.emit({"kernel_id": kid, "blocks": blocks}, plain, output.history)


@app.command("export-history")
def export_history(
    out_path: str = typer.Argument(..., help="Output .ipynb path."),
    ids: Optional[str] = typer.Option(
        None, "--ids", help="Comma-separated block ids to export. Defaults to all blocks."
    ),
    kernel_id: Optional[str] = KernelIdOpt,
    plain: bool = PlainOpt,
):
    """Select code blocks in the history by ids and export them to a notebook."""
    try:
        kid = state.resolve_kernel_id(kernel_id)
    except state.StateError as exc:
        _fail(str(exc), plain)
    if ids is None:
        block_ids = [b["block_id"] for b in state.load_history(kid)]
    else:
        block_ids = [i.strip() for i in ids.split(",") if i.strip()]
    if not block_ids:
        _fail("No block ids given (use --ids a,b,c).", plain)
    try:
        result = notebook.export(kid, block_ids, out_path)
    except KeyError as exc:
        _fail(str(exc).strip('"'), plain)
    result["message"] = f"Exported {result['cells']} cell(s) to {result['path']}."
    output.emit(result, plain, output.message)


if __name__ == "__main__":
    app()
