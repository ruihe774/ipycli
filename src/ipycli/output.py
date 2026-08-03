"""Output formatting: JSON (default, for agents) or plain human-readable text."""

import json
import sys
from typing import Any


def emit(data: Any, plain: bool, plain_formatter=None) -> None:
    """Print ``data`` as JSON, or via ``plain_formatter`` when ``plain`` is set."""
    if plain and plain_formatter is not None:
        text = plain_formatter(data)
        if text:
            print(text)
    else:
        print(json.dumps(data, indent=2))


def _fmt_outputs(outputs: list[dict], plots: list[str], indent: str = "  ") -> str:
    lines = []
    for o in outputs:
        t = o["type"]
        if t == "stream":
            for line in o.get("text", "").splitlines():
                lines.append(f"{indent}[{o.get('name', 'stdout')}] {line}")
        elif t in ("execute_result", "display_data"):
            for line in o.get("text", "").splitlines():
                lines.append(f"{indent}=> {line}")
        elif t == "error":
            lines.append(f"{indent}!! {o.get('ename')}: {o.get('evalue')}")
    for p in plots:
        lines.append(f"{indent}[plot] {p}")
    return "\n".join(lines)


# --- per-command plain formatters -----------------------------------------


def kernelspecs(data: list[dict]) -> str:
    return "\n".join(
        f"{s['name']:20} {s['display_name']}  ({s['language']})" for s in data
    )


def kernels(data: list[dict]) -> str:
    if not data:
        return "No running kernels."
    return "\n".join(
        f"{k['kernel_id']}  spec={k['spec']:12} pid={k['pid']}  started={k['started_at']}"
        for k in data
    )


def launched(data: dict) -> str:
    return (
        f"Launched kernel {data['kernel_id']} "
        f"(spec={data['spec']}, pid={data['pid']}, cwd={data.get('cwd', '')})"
    )


def execution(data: dict) -> str:
    blocks = data.get("blocks", [data]) if "blocks" in data else [data]
    parts = []
    for b in blocks:
        header = f"[{b['block_id']}] status={b['status']}"
        body = _fmt_outputs(b.get("outputs", []), b.get("plots", []))
        parts.append(header + ("\n" + body if body else ""))
    return "\n".join(parts)


def variables(data: list[dict]) -> str:
    if not data:
        return "No variables defined."
    return "\n".join(f"{v['name']:20} {v['type']:12} {v['repr']}" for v in data)


def history(data: dict) -> str:
    blocks = data.get("blocks", [])
    if not blocks:
        return "No history."
    parts = []
    for b in blocks:
        parts.append(f"=== [{b['block_id']}] status={b.get('status')} ===")
        parts.append(b.get("code", "").rstrip())
        body = _fmt_outputs(b.get("outputs", []), b.get("plots", []))
        if body:
            parts.append(body)
        parts.append("")
    return "\n".join(parts)


def jobs(data: dict) -> str:
    job_list = data.get("jobs", [])
    if not job_list:
        return "No background jobs."
    parts = []
    for j in job_list:
        head = f"=== job {j['job_id']} status={j.get('status')}"
        if j.get("reaped"):
            head += " (reaped)"
        head += " ==="
        parts.append(head)
        if j.get("error"):
            parts.append(f"  error: {j['error']}")
        for b in j.get("results") or []:
            parts.append(f"  [{b.get('block_id')}] status={b.get('status')}")
            body = _fmt_outputs(b.get("outputs", []), b.get("plots", []), indent="    ")
            if body:
                parts.append(body)
    return "\n".join(parts)


def message(data: dict) -> str:
    return data.get("message", json.dumps(data))
