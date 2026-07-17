---
name: ipycli
description: Execute code on persistent IPython/Jupyter kernels from the command line. Use when you need to run Python (or other-kernel) code with state that survives across commands — an interactive REPL-like session, incremental data analysis, inspecting variables between steps, or capturing matplotlib plots. Triggers include "run this in a kernel", "keep the session alive", "what variables are defined", "show/export what we ran", or any multi-step compute where results carry over between calls.
---

# ipycli

`ipycli` drives **persistent IPython kernels** from the shell. A kernel launched
once keeps running (detached, in the background) and every later command executes
against it, so variables, imports, and loaded data **persist between invocations**.
This is the tool to reach for instead of one-shot `python -c` when you need state
to carry across steps.

Run it with `ipycli <command>`. It's installed on PATH.

## Output contract

- Every command prints **JSON to stdout** by default — parse this. Add `--plain`
  only when a human is reading directly.
- Errors print `{"error": "..."}` to **stderr** and exit non-zero.
- Code that raises inside the kernel is **not** a CLI error: the command exits 0
  and the block's JSON has `"status": "error"` with `ename`/`evalue`/`traceback`.
  Always check the `status` field, not just the exit code.

## Core workflow

1. **Launch once**, capture the id:
   ```bash
   KID=$(ipycli launch-kernel python3 | jq -r .kernel_id)
   ```
   `python3` is the default spec; run `list-kernelspecs` to see what's installed.

2. **Execute** repeatedly against it. Three input sources:
   ```bash
   ipycli execute-code -c "x = 21 * 2; print(x)"      # inline
   ipycli execute-code analysis.py                    # a .py file
   ipycli execute-code notebook.ipynb                 # each code cell = 1 block
   echo "print(x)" | ipycli execute-code              # stdin
   ```
   Each block gets a unique `block_id`. State carries over: a later block sees
   `x` from an earlier one.

   By default `execute-code` **blocks** until the code finishes (up to
   `--timeout`/`-t` seconds, default 300). For long-running work, add
   `--background`/`-b`: the command returns a `job_id` immediately and the code
   runs detached (default timeout 3600s). Collect the result later with
   `poll-background`.

3. **Inspect / manage** as needed (see command table).

## Background execution

- `execute-code -b` runs detached and prints `{job_id, block_ids, status: "running"}`.
- `poll-background` reports each job's status. A **finished** job includes its
  block `results` and is then **reaped** (removed) — pass `--keep` to leave it,
  or `--wait` to block until it finishes. Target one job with `--job-id`/`-j`.
- **Only one execution runs at a time per kernel.** An IPython kernel executes
  requests serially, so background code shares the same kernel and state as
  everything else. While a background job is in flight, other `execute-code`
  calls on that kernel are **refused** until you reap it with `poll-background`.

## Kernel targeting

Commands that act on a kernel take `--kernel-id`/`-k`. **Omit it when exactly one
kernel is running** and that kernel is used automatically. With zero or multiple
kernels running and no `-k`, the command errors — pass `-k <id>` explicitly.

## Commands

| Command | What it does |
|---|---|
| `list-kernelspecs` | Installed kernel specs (name, language) |
| `launch-kernel [SPEC]` | Launch detached kernel (default `python3`); returns `kernel_id` |
| `list-kernels` | Running kernels (dead ones pruned automatically) |
| `execute-code [-c CODE \| FILE] [-k ID] [-t SECS] [-b]` | Run code from `-c`, a `.py`/`.ipynb` file, or stdin |
| `poll-background [-k ID] [-j JOB] [--wait]` | Monitor and reap background jobs (from `execute-code -b`) |
| `list-variables [-k ID]` | User-defined variables: name, type, repr preview |
| `restart-kernel [-k ID]` | Fresh kernel process, **clears all history & variables** |
| `stop-kernel [-k ID]` | Terminate and deregister the kernel |
| `show-history [-k ID]` | All run blocks with ids, code, and outputs |
| `export-history OUT.ipynb --ids a,b,c [-k ID]` | Export selected blocks to a `.ipynb` |

## JSON output schemas

Field types below; `?` marks nullable/optional.

**`list-kernelspecs`** — array of:
```jsonc
{"name": "python3", "display_name": "Python 3", "language": "python", "resource_dir": "/path"}
```

**`launch-kernel`** — one object:
```jsonc
{"kernel_id": "a1b2c3d4", "spec": "python3", "language": "python", "pid": 12345, "started_at": "2026-07-16T12:00:00+00:00", "cwd": "/path"}
```

**`list-kernels`** — array of the same object shape as `launch-kernel`.

**`execute-code`** — a single block object if one block ran, or `{"kernel_id": ..., "blocks": [block, ...]}` for multiple (e.g. from a `.ipynb`/multi-cell file):
```jsonc
{
  "block_id": "e5f6a7b8",
  "status": "ok",              // "ok" | "error" | "timeout"
  "execution_count": 3,        // int | null
  "outputs": [
    {"type": "stream", "name": "stdout", "text": "..."},
    {"type": "execute_result", "text": "..."},   // or "display_data"
    {"type": "error", "ename": "ValueError", "evalue": "...", "traceback": ["..."]}
  ],
  "plots": ["/path/to/plot0.png"]
}
```
Note: the top-level `execute-code` payload also carries `"code"` and `"ts"` fields (from history) when a single block is returned; the `blocks` array entries do too.

With `--background`, `execute-code` instead returns immediately with:
```jsonc
{"kernel_id": "...", "job_id": "6a3f8178", "status": "running", "block_ids": ["c4e21f9b"], "timeout": 3600.0, "message": "..."}
```

**`poll-background`** — `{"kernel_id": "...", "jobs": [job, ...]}` where each job is:
```jsonc
{
  "job_id": "6a3f8178",
  "status": "running",         // "running" | "done" | "error" | "failed"
  "block_ids": ["c4e21f9b"],
  "started_at": "...", "finished_at": "..." /* | null */,
  "timeout": 3600.0,
  "results": [block, ...],     // null while running; block objects once finished
  "error": null,               // string on "error"/"failed"
  "reaped": true               // true if this call removed the job's record
}
```

**`list-variables`** — array of:
```jsonc
{"name": "df", "type": "DataFrame", "repr": "<preview string>"}
```

**`restart-kernel` / `stop-kernel`** — `{"kernel_id": "...", "message": "..."}`

**`show-history`** — `{"kernel_id": "...", "blocks": [block, ...]}` where each block is:
```jsonc
{"block_id": "...", "code": "...", "status": "ok", "execution_count": 3, "outputs": [...], "plots": [...], "ts": "2026-07-16T12:00:00+00:00"}
```

**`export-history`** — `{"path": "session.ipynb", "cells": 3, "message": "Exported 3 cell(s) to session.ipynb."}`

**Errors** (stderr, any command) — `{"error": "message"}`

## Plots

Matplotlib and R figures are **captured automatically** — no `savefig` needed. When a
block produces a figure (e.g. `plt.plot(...)`), it is saved as a PNG and its path
appears in that block's `"plots"` array in the JSON result. To view it, read the
returned path.

## Notes & good habits

- State lives under `~/.ipycli/` (override with the `IPYCLI_HOME` env var).
- To reproduce or hand off work, `export-history` selected `block_id`s (get them
  from `show-history`) into a runnable notebook.
- Use `restart-kernel` to get a clean slate; it discards variables **and** history.
- Clean up long-running sessions with `stop-kernel` when done.

## Example: end-to-end analysis session

```bash
KID=$(ipycli launch-kernel python3 | jq -r .kernel_id)
ipycli execute-code -c "import pandas as pd; df = pd.read_csv('data.csv')"
ipycli execute-code -c "print(df.describe())"          # df persists
ipycli execute-code -c "df['x'].plot()"                # PNG path returned
ipycli list-variables                                  # confirm df is there
IDS=$(ipycli show-history | jq -r '[.blocks[].block_id] | join(",")')
ipycli export-history session.ipynb --ids "$IDS"
ipycli stop-kernel
```
