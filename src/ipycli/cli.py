"""ipycli command-line interface.

A CLI for LLM coding agents to execute code with IPython kernels. Every command
defaults to JSON output (for reliable machine parsing) and accepts ``--plain``
for human-readable text.
"""

from __future__ import annotations

import sys
from typing import Optional

import typer

from . import kernel, notebook, output, state

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
        import json

        print(json.dumps({"error": message}, indent=2), file=sys.stderr)
    raise typer.Exit(code=1)


@app.command("list-kernelspecs")
def list_kernelspecs(plain: bool = PlainOpt):
    """List the installed kernel specs."""
    output.emit(kernel.list_kernelspecs(), plain, output.kernelspecs)


@app.command("launch-kernel")
def launch_kernel(
    spec: str = typer.Argument("python3", help="Kernel spec name (see list-kernelspecs)."),
    plain: bool = PlainOpt,
):
    """Launch a kernel in the background (detached) and print its unique id."""
    try:
        kernel_id = kernel.launch(spec)
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

    client = kernel.connect(kid)
    results = []
    try:
        for src in blocks_src:
            results.append(kernel.execute(kid, src, client=client))
    except kernel.KernelError as exc:
        _fail(str(exc), plain)
    finally:
        client.stop_channels()

    payload = results[0] if len(results) == 1 else {"kernel_id": kid, "blocks": results}
    output.emit(payload, plain, output.execution)


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
    ids: str = typer.Option(..., "--ids", help="Comma-separated block ids to export."),
    kernel_id: Optional[str] = KernelIdOpt,
    plain: bool = PlainOpt,
):
    """Select code blocks in the history by ids and export them to a notebook."""
    try:
        kid = state.resolve_kernel_id(kernel_id)
    except state.StateError as exc:
        _fail(str(exc), plain)
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
