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

3. **Inspect / manage** as needed (see command table).

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
| `execute-code [-c CODE \| FILE] [-k ID]` | Run code from `-c`, a `.py`/`.ipynb` file, or stdin |
| `list-variables [-k ID]` | User-defined variables: name, type, repr preview |
| `restart-kernel [-k ID]` | Fresh kernel process, **clears all history & variables** |
| `stop-kernel [-k ID]` | Terminate and deregister the kernel |
| `show-history [-k ID]` | All run blocks with ids, code, and outputs |
| `export-history OUT.ipynb --ids a,b,c [-k ID]` | Export selected blocks to a `.ipynb` |

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
