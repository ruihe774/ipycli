# ipycli

A CLI for LLM coding agents to execute code with IPython (Jupyter) kernels.

Kernels run **detached in the background** and outlive the CLI process, so an
agent can launch a kernel once and then run many code blocks against it across
separate invocations. All state (running kernels, per-block history, captured
plots) is persisted under `~/.ipycli/` (override with `IPYCLI_HOME`).

Every command prints **JSON** by default (easy to parse); pass `--plain` for
human-readable output.

## Install / run

```bash
uv sync
uv run python -m ipykernel install --user   # ensure a python3 kernel spec exists
uv run ipycli --help
```

## Commands

| Command | Purpose |
|---|---|
| `list-kernelspecs` | List installed kernel specs |
| `launch-kernel [SPEC]` | Launch a detached kernel (default `python3`); prints a unique id |
| `list-kernels` | List running kernels |
| `execute-code [-c CODE \| FILE] [-k ID]` | Execute code from `-c`, a `.py`/`.ipynb` file, or stdin |
| `list-variables [-k ID]` | List variables defined in the kernel |
| `restart-kernel [-k ID]` | Restart the kernel and clear history |
| `stop-kernel [-k ID]` | Stop the kernel |
| `show-history [-k ID]` | Show run code blocks with their ids and outputs |
| `export-history OUT.ipynb --ids a,b [-k ID]` | Export selected blocks to a notebook |

`--kernel-id`/`-k` may be omitted when exactly one kernel is running.

## Example

```bash
KID=$(uv run ipycli launch-kernel python3 | jq -r .kernel_id)
uv run ipycli execute-code -c "x = 21 * 2; print(x)"
uv run ipycli execute-code -c "import matplotlib.pyplot as plt; plt.plot([1,2,3]); plt.show()"
uv run ipycli list-variables
uv run ipycli show-history
uv run ipycli export-history out.ipynb --ids <id1>,<id2>
uv run ipycli stop-kernel
```

Plots are captured automatically: any figure a block produces is saved as a PNG
under `~/.ipycli/kernels/<id>/outputs/` and its path is returned in the block's
result.
