"""Reading code input (-c / file / stdin) and exporting history to a notebook."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

import nbformat

from . import state


def read_blocks(
    code: Optional[str], file: Optional[str]
) -> list[str]:
    """Return the list of code blocks to execute from the chosen input source.

    - ``-c CODE``: a single block.
    - a ``.ipynb`` file: one block per code cell.
    - any other file: a single block (whole file).
    - neither: read a single block from stdin.
    """
    if code is not None:
        return [code]

    if file is not None:
        path = Path(file)
        if not path.exists():
            raise FileNotFoundError(f"No such file: {file}")
        if path.suffix == ".ipynb":
            nb = nbformat.read(str(path), as_version=4)
            return [
                cell.source
                for cell in nb.cells
                if cell.cell_type == "code" and cell.source.strip()
            ]
        return [path.read_text()]

    data = sys.stdin.read()
    if not data.strip():
        return []
    return [data]


def _outputs_to_nb(outputs: list[dict[str, Any]], plots: list[str]) -> list[Any]:
    """Convert stored block outputs into nbformat output nodes."""
    nb_outputs: list[Any] = []
    for o in outputs:
        t = o["type"]
        if t == "stream":
            nb_outputs.append(
                nbformat.v4.new_output(
                    "stream", name=o.get("name", "stdout"), text=o.get("text", "")
                )
            )
        elif t in ("execute_result", "display_data"):
            nb_outputs.append(
                nbformat.v4.new_output(
                    t, data={"text/plain": o.get("text", "")}
                )
            )
        elif t == "error":
            nb_outputs.append(
                nbformat.v4.new_output(
                    "error",
                    ename=o.get("ename", ""),
                    evalue=o.get("evalue", ""),
                    traceback=o.get("traceback", []),
                )
            )
    # Attach captured plots as image outputs.
    import base64

    for plot in plots:
        p = Path(plot)
        if p.exists():
            png_b64 = base64.b64encode(p.read_bytes()).decode("ascii")
            nb_outputs.append(
                nbformat.v4.new_output("display_data", data={"image/png": png_b64})
            )
    return nb_outputs


def export(kernel_id: str, block_ids: list[str], out_path: str) -> dict[str, Any]:
    """Export selected history blocks to a Jupyter notebook."""
    history = state.load_history(kernel_id)
    by_id = {b["block_id"]: b for b in history}

    missing = [bid for bid in block_ids if bid not in by_id]
    if missing:
        raise KeyError(f"Unknown block id(s): {', '.join(missing)}")

    nb = nbformat.v4.new_notebook()
    nb.metadata["ipycli"] = {"kernel_id": kernel_id}
    # Stamp the kernelspec so the notebook opens with the right kernel/language.
    meta = state.load_meta(kernel_id) or {}
    spec = meta.get("spec")
    if spec:
        nb.metadata["kernelspec"] = {"name": spec, "display_name": spec}
        language = meta.get("language")
        if language:
            nb.metadata["language_info"] = {"name": language}
    cells = []
    for bid in block_ids:
        block = by_id[bid]
        cell = nbformat.v4.new_code_cell(source=block["code"])
        cell.execution_count = block.get("execution_count")
        cell.outputs = _outputs_to_nb(block.get("outputs", []), block.get("plots", []))
        cells.append(cell)
    nb.cells = cells

    out = Path(out_path)
    if out.parent and not out.parent.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
    nbformat.write(nb, str(out))
    return {"path": str(out), "cells": len(cells)}
