---
name: ipycli
description: Execute code on persistent IPython/Jupyter kernels from the command line. Use when you need to run Python (or other-kernel) code with state that survives across commands — an interactive REPL-like session, incremental data analysis, inspecting variables between steps, or capturing matplotlib plots. Triggers include "run this in a kernel", "keep the session alive", "what variables are defined", "show/export what we ran", or any multi-step compute where results carry over between calls.
---

# ipycli

CLI for persistent, detached IPython kernels: state (vars/imports) survives across `ipycli` invocations. Installed on PATH.

## Rules

- All commands print JSON to stdout (`--plain` for human-readable). Errors → `{"error": "..."}` on stderr, non-zero exit.
- Code raising inside the kernel is NOT a CLI error: exit 0, block JSON has `"status": "error"` + `ename`/`evalue`/`traceback`. **Always check `status`, not exit code.**
- `--kernel-id`/`-k`: omit only if exactly one kernel is running (auto-used). With 0 or 2+ kernels, omitting errors — pass `-k` explicitly.
- Only one execution runs per kernel at a time. While a background job is in flight, other `execute-code` calls on that kernel are refused until reaped via `poll-background`.
- Plots (matplotlib/R) are auto-captured as PNG, no `savefig` needed; path in block's `"plots"` array.
- State lives under `~/.ipycli/` (override: `IPYCLI_HOME`).

## Workflow

```bash
KID=$(ipycli launch-kernel python3 | jq -r .kernel_id)   # python3 is default spec
ipycli execute-code -c "x = 21 * 2; print(x)"            # inline
ipycli execute-code analysis.py                          # .py file
ipycli execute-code notebook.ipynb                       # each cell = 1 block
echo "print(x)" | ipycli execute-code                    # stdin
```

Background: `execute-code -b`/`--background` returns `{job_id, block_ids, status:"running"}` immediately (default timeout 3600s vs 300s foreground). Reap with `poll-background` (`--wait` to block, `--keep` to not reap, `-j` to target one job).

## Commands

| Command | Args | Does |
|---|---|---|
| `list-kernelspecs` | | Installed kernel specs |
| `launch-kernel [SPEC]` | | Launch detached kernel (default `python3`) → `kernel_id` |
| `list-kernels` | | Running kernels (dead ones pruned) |
| `execute-code` | `[-c CODE \| FILE] [-k ID] [-t SECS] [-b]` | Run code (inline/file/stdin) |
| `poll-background` | `[-k ID] [-j JOB] [--wait] [--keep]` | Monitor/reap background jobs |
| `list-variables` | `[-k ID]` | User-defined vars: name, type, repr |
| `restart-kernel` | `[-k ID]` | Fresh process; clears vars + history |
| `stop-kernel` | `[-k ID]` | Terminate + deregister |
| `show-history` | `[-k ID]` | All run blocks (id, code, outputs) |
| `export-history` | `OUT.ipynb [--ids a,b,c] [-k ID]` | Export blocks to notebook |

## JSON schemas

`?` = nullable/optional.

```jsonc
// launch-kernel / list-kernels item
{"kernel_id": "a1b2c3d4", "spec": "python3", "language": "python", "pid": 12345, "started_at": "iso8601", "cwd": "/path", "connection_file": "/path/connection.json"}

// execute-code: single block, or {"kernel_id", "blocks": [block,...]} for multi-cell input
{
  "block_id": "e5f6a7b8",
  "status": "ok",              // "ok" | "error" | "timeout"
  "execution_count": 3,        // int?
  "outputs": [
    {"type": "stream", "name": "stdout", "text": "..."},
    {"type": "execute_result", "text": "..."},   // or "display_data"
    {"type": "error", "ename": "ValueError", "evalue": "...", "traceback": ["..."]}
  ],
  "plots": ["/path/plot0.png"]
}

// execute-code --background
{"kernel_id": "...", "job_id": "6a3f8178", "status": "running", "block_ids": ["c4e21f9b"], "timeout": 3600.0, "message": "..."}

// poll-background
{"kernel_id": "...", "jobs": [
  {"job_id": "...", "status": "running|done|error|failed", "block_ids": [...],
   "started_at": "...", "finished_at": "...?", "timeout": 3600.0,
   "results": null, "error": null, "reaped": true}  // results: block[] once finished
]}

// list-variables item
{"name": "df", "type": "DataFrame", "repr": "<preview>"}

// restart-kernel / stop-kernel
{"kernel_id": "...", "message": "..."}

// show-history
{"kernel_id": "...", "blocks": [{"block_id","code","status","execution_count","outputs","plots","ts"}, ...]}

// export-history
{"path": "session.ipynb", "cells": 3, "message": "..."}
```

`connection_file` (from launch-kernel) can attach an external client: `jupyter console --existing <connection_file>`.

Get `block_id`s from `show-history` to select which blocks `export-history` writes to a notebook.
