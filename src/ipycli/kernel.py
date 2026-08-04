"""The kernel engine: launch (detached), connect, execute, restart, stop.

Kernels are launched as detached background processes so they outlive the
short-lived CLI invocation that starts them. We generate our own connection
file, spawn the kernelspec's ``argv`` with ``start_new_session=True``, and
reconnect from later invocations via a ``BlockingKernelClient``.
"""

import base64
import json
import os
import signal
import subprocess
import time
from datetime import datetime, timezone
from queue import Empty
from typing import Any, Optional

from jupyter_client import BlockingKernelClient
from jupyter_client.connect import write_connection_file
from jupyter_client.kernelspec import KernelSpecManager, NoSuchKernel

from . import state

# How long to wait for a freshly launched kernel to become ready.
READY_TIMEOUT = 60.0
# How long a single code block may run before we give up draining messages.
# This is the default for foreground (blocking) execution; ``--timeout`` on
# ``execute-code`` overrides it.
EXEC_TIMEOUT = 300.0
# Default per-block timeout for background execution. A detached background job
# is meant for long-running work, so it gets a much larger budget than the
# interactive foreground default.
BG_EXEC_TIMEOUT = 3600.0

# Bootstrap run once per kernel so matplotlib figures arrive as PNG display
# data that we can capture to disk automatically.
BOOTSTRAP_CODE = (
    "import matplotlib\n"
    "matplotlib.use('module://matplotlib_inline.backend_inline')\n"
    "get_ipython().run_line_magic('matplotlib', 'inline')\n"
)


class KernelError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- launch ----------------------------------------------------------------


def list_kernelspecs() -> list[dict[str, Any]]:
    ksm = KernelSpecManager()
    specs = ksm.get_all_specs()
    out = []
    for name, info in sorted(specs.items()):
        spec = info.get("spec", {})
        out.append(
            {
                "name": name,
                "display_name": spec.get("display_name", name),
                "language": spec.get("language", ""),
                "resource_dir": info.get("resource_dir", ""),
            }
        )
    return out


def launch(spec_name: str, cwd: Optional[str] = None) -> str:
    """Launch a detached kernel for ``spec_name`` and return its kernel id.

    ``cwd`` is the working directory the kernel process runs in; defaults to
    the caller's current directory.
    """
    ksm = KernelSpecManager()
    try:
        spec = ksm.get_kernel_spec(spec_name)
    except NoSuchKernel as exc:
        raise KernelError(f"No such kernel spec: {spec_name}") from exc

    work_dir = os.path.abspath(cwd) if cwd else os.getcwd()
    if not os.path.isdir(work_dir):
        raise KernelError(f"No such directory: {work_dir}")

    kernel_id = state.new_id()
    kdir = state.create_kernel_dir(kernel_id)
    conn_file = str(kdir / "connection.json")

    # Generate ports + HMAC key and write the connection file ourselves.
    write_connection_file(fname=conn_file, kernel_name=spec_name)

    pid = _spawn(spec, conn_file, kdir, work_dir)

    meta = {
        "kernel_id": kernel_id,
        "spec": spec_name,
        "language": spec.language,
        "pid": pid,
        "started_at": _now(),
        "connection_file": conn_file,
        "cwd": work_dir,
        "bootstrapped": False,
    }
    state.save_meta(kernel_id, meta)
    return kernel_id


def _spawn(spec, conn_file: str, kdir, work_dir: str) -> int:
    """Spawn the kernel process detached; return its pid."""
    argv = [
        arg.replace("{connection_file}", conn_file) for arg in spec.argv
    ]
    env = os.environ.copy()
    env.update(spec.env or {})

    log = open(kdir / "kernel.log", "wb")
    proc = subprocess.Popen(
        argv,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,  # detach: survives parent exit
        cwd=work_dir,
    )
    return proc.pid


# --- connect ---------------------------------------------------------------


def connect(kernel_id: str, ready_timeout: float = READY_TIMEOUT):
    """Return a started, ready BlockingKernelClient for ``kernel_id``."""
    meta = state.load_meta(kernel_id)
    if meta is None:
        raise KernelError(f"No such kernel: {kernel_id}")

    client = BlockingKernelClient(connection_file=meta["connection_file"])
    client.load_connection_file()
    client.start_channels()
    try:
        client.wait_for_ready(timeout=ready_timeout)
    except RuntimeError as exc:
        client.stop_channels()
        raise KernelError(f"Kernel {kernel_id} did not become ready: {exc}") from exc
    return client


def _ensure_bootstrapped(kernel_id: str, client) -> None:
    meta = state.load_meta(kernel_id) or {}
    if meta.get("bootstrapped"):
        return
    # Only Python kernels need the matplotlib-inline bootstrap. Other kernels
    # (e.g. IRkernel) emit plots as image/png natively, so running Python setup
    # code there would just be a no-op error.
    if str(meta.get("language", "")).lower() == "python":
        try:
            _run(
                client, BOOTSTRAP_CODE, kernel_id, block_id="",
                silent=False, store_history=False,
            )
        except Exception:
            pass
    meta["bootstrapped"] = True
    state.save_meta(kernel_id, meta)


# --- execute ---------------------------------------------------------------


def execute(
    kernel_id: str,
    code: str,
    silent: bool = False,
    client=None,
    block_id: Optional[str] = None,
    timeout: Optional[float] = None,
) -> dict[str, Any]:
    """Execute ``code`` on the kernel and return a structured result.

    When ``silent`` is False the block is recorded to history. A ``block_id``
    is generated if not provided. ``timeout`` caps how long the block may run
    before its status is reported as ``"timeout"``; it defaults to
    :data:`EXEC_TIMEOUT`.
    """
    own_client = client is None
    if own_client:
        client = connect(kernel_id)
    try:
        if not silent:
            _ensure_bootstrapped(kernel_id, client)
        bid = block_id or state.new_id()
        result = _run(client, code, kernel_id, bid, silent=silent, timeout=timeout)
        if not silent:
            block = {
                "block_id": bid,
                "code": code,
                "status": result["status"],
                "execution_count": result.get("execution_count"),
                "outputs": result["outputs"],
                "plots": result["plots"],
                "ts": _now(),
            }
            state.append_history(kernel_id, block)
        return result
    finally:
        if own_client:
            client.stop_channels()


def _run(
    client,
    code: str,
    kernel_id: str,
    block_id: str,
    silent: bool,
    store_history: Optional[bool] = None,
    timeout: Optional[float] = None,
) -> dict[str, Any]:
    """Send one execute request and drain its messages until idle.

    ``silent`` maps to the Jupyter execute_request flag — but note some kernels
    (e.g. IRkernel) suppress *all* IOPub output when silent, so for internal
    introspection we want ``silent=False, store_history=False`` instead: output
    is still broadcast (so we can read it) but the execution count and the
    kernel's own history are left untouched.
    """
    if store_history is None:
        store_history = not silent
    msg_id = client.execute(
        code, silent=silent, store_history=store_history, allow_stdin=False
    )

    outputs: list[dict[str, Any]] = []
    plots: list[str] = []
    status = "ok"
    execution_count: Optional[int] = None
    plot_n = 0

    exec_timeout = EXEC_TIMEOUT if timeout is None else timeout
    deadline = time.monotonic() + exec_timeout
    idle = False
    while not idle:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            status = "timeout"
            _interrupt(kernel_id)
            break
        try:
            msg = client.get_iopub_msg(timeout=min(remaining, 1.0))
        except Empty:
            continue
        if msg.get("parent_header", {}).get("msg_id") != msg_id:
            continue

        mtype = msg["msg_type"]
        content = msg["content"]

        if mtype == "status":
            if content.get("execution_state") == "idle":
                idle = True
        elif mtype == "stream":
            outputs.append(
                {"type": "stream", "name": content.get("name", "stdout"),
                 "text": content.get("text", "")}
            )
        elif mtype in ("execute_result", "display_data"):
            data = content.get("data", {})
            plot_n = _capture_plots(data, kernel_id, block_id, plot_n, plots)
            if "text/plain" in data:
                outputs.append({"type": mtype, "text": data["text/plain"]})
        elif mtype == "error":
            status = "error"
            outputs.append(
                {
                    "type": "error",
                    "ename": content.get("ename", ""),
                    "evalue": content.get("evalue", ""),
                    "traceback": content.get("traceback", []),
                }
            )
        elif mtype == "execute_input":
            execution_count = content.get("execution_count")

    # Collect the shell reply for the final status / execution_count.
    try:
        reply = client.get_shell_msg(timeout=5.0)
        if reply.get("parent_header", {}).get("msg_id") == msg_id:
            rc = reply["content"]
            if rc.get("status") == "error" and status == "ok":
                status = "error"
            if rc.get("execution_count") is not None:
                execution_count = rc["execution_count"]
    except Empty:
        pass

    return {
        "block_id": block_id,
        "status": status,
        "execution_count": execution_count,
        "outputs": outputs,
        "plots": plots,
    }


def _capture_plots(
    data: dict, kernel_id: str, block_id: str, plot_n: int, plots: list[str]
) -> int:
    """Save any image/png payload in ``data`` to the outputs dir."""
    png = data.get("image/png")
    if not png:
        return plot_n
    out_dir = state.kernel_dir(kernel_id) / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{block_id or 'x'}_{plot_n}.png"
    path.write_bytes(base64.b64decode(png))
    plots.append(str(path))
    return plot_n + 1


# --- introspection ---------------------------------------------------------


# Listing variables has no standard Jupyter protocol message, so we run
# language-specific introspection code and parse its stdout. It runs with
# store_history=False (not silent) so output is broadcast on every kernel
# without bumping the execution count.

_PY_LIST_VARS_CODE = r"""
import json as __ipycli_json, types as __ipycli_types
def __ipycli_list_vars():
    ns = get_ipython().user_ns
    hidden = set(get_ipython().user_ns_hidden)
    out = []
    for name, val in ns.items():
        if name.startswith('_') or name in hidden:
            continue
        if isinstance(val, __ipycli_types.ModuleType):
            continue
        try:
            r = repr(val)
        except Exception:
            r = '<unreprable>'
        if len(r) > 200:
            r = r[:200] + '...'
        out.append({'name': name, 'type': type(val).__name__, 'repr': r})
    print(__ipycli_json.dumps(out))
__ipycli_list_vars()
del __ipycli_list_vars
"""

# R introspection. Emits records with control-char field/record separators
# (\x1f / \x1e) to sidestep JSON escaping and any dependency on jsonlite.
_R_LIST_VARS_CODE = (
    "local({\n"
    "  .ipycli_vars <- ls(envir = .GlobalEnv)\n"
    "  for (.ipycli_n in .ipycli_vars) {\n"
    "    .ipycli_v <- get(.ipycli_n, envir = .GlobalEnv)\n"
    "    .ipycli_c <- paste(class(.ipycli_v), collapse = '/')\n"
    "    .ipycli_r <- tryCatch(paste(utils::capture.output(print(.ipycli_v)), collapse = ' '),\n"
    "                          error = function(e) '<unprintable>')\n"
    "    if (nchar(.ipycli_r) > 200) .ipycli_r <- paste0(substr(.ipycli_r, 1, 200), '...')\n"
    "    cat(.ipycli_n, '\\x1f', .ipycli_c, '\\x1f', .ipycli_r, '\\x1e', sep = '')\n"
    "  }\n"
    "})\n"
)


def _parse_r_vars(text: str) -> list[dict[str, Any]]:
    out = []
    for record in text.split("\x1e"):
        record = record.strip()
        if not record:
            continue
        parts = record.split("\x1f")
        if len(parts) >= 3:
            out.append(
                {"name": parts[0].strip(), "type": parts[1].strip(),
                 "repr": parts[2].strip()}
            )
    return out


def list_variables(kernel_id: str) -> list[dict[str, Any]]:
    meta = state.load_meta(kernel_id) or {}
    language = str(meta.get("language", "")).lower()
    if language == "python":
        code = _PY_LIST_VARS_CODE
    elif language == "r":
        code = _R_LIST_VARS_CODE
    else:
        raise KernelError(
            f"list-variables is not supported for '{language or 'unknown'}' kernels."
        )

    client = connect(kernel_id)
    try:
        result = _run(
            client, code, kernel_id, block_id="",
            silent=False, store_history=False,
        )
    finally:
        client.stop_channels()
    text = "".join(
        o["text"] for o in result["outputs"] if o["type"] == "stream"
    )

    if language == "r":
        return _parse_r_vars(text)

    try:
        return json.loads(text.strip() or "[]")
    except json.JSONDecodeError:
        return []


# --- lifecycle -------------------------------------------------------------


def _interrupt(kernel_id: str) -> None:
    """Send SIGINT to the kernel process so timed-out code actually stops.

    Without this, a block that hits its deadline keeps running in the kernel
    after we give up draining its messages, and every subsequent
    execute-request on that kernel silently queues behind it (ipykernel
    processes the shell channel strictly serially).
    """
    meta = state.load_meta(kernel_id)
    if meta is None:
        return
    pid = int(meta.get("pid", -1))
    if pid <= 0 or not state.pid_alive(pid):
        return
    try:
        os.kill(pid, signal.SIGINT)
    except ProcessLookupError:
        pass


def _kill(pid: int) -> None:
    if not state.pid_alive(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    for _ in range(30):
        if not state.pid_alive(pid):
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def restart(kernel_id: str) -> None:
    """Restart the kernel (fresh process, same id) and clear its history."""
    meta = state.load_meta(kernel_id)
    if meta is None:
        raise KernelError(f"No such kernel: {kernel_id}")

    _kill(int(meta.get("pid", -1)))

    kdir = state.kernel_dir(kernel_id)
    conn_file = meta["connection_file"]
    # Regenerate connection file (fresh ports/key) for the new process.
    write_connection_file(fname=conn_file, kernel_name=meta["spec"])

    ksm = KernelSpecManager()
    spec = ksm.get_kernel_spec(meta["spec"])
    work_dir = meta.get("cwd") or os.getcwd()
    pid = _spawn(spec, conn_file, kdir, work_dir)

    meta.update(
        {
            "pid": pid,
            "started_at": _now(),
            "bootstrapped": False,
        }
    )
    state.save_meta(kernel_id, meta)
    state.clear_history(kernel_id)


def stop(kernel_id: str) -> None:
    """Terminate the kernel and remove it from the registry."""
    meta = state.load_meta(kernel_id)
    if meta is None:
        raise KernelError(f"No such kernel: {kernel_id}")
    _kill(int(meta.get("pid", -1)))
    state.remove_kernel(kernel_id)
