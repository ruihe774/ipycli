# ipycli

A CLI for LLM coding agents to execute code with IPython (Jupyter) kernels.

Kernels run **detached in the background** and outlive the CLI process, so an
agent can launch a kernel once and then run many code blocks against it across
separate invocations. All state (running kernels, per-block history, captured
plots) is persisted under `~/.ipycli/` (override with `IPYCLI_HOME`).

Every command prints **JSON** by default (easy to parse); pass `--plain` for
human-readable output.

## Install

Install `ipycli` onto your `PATH` with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install .                           # from a checkout of this repo
```

Then ensure a `python3` kernel spec is registered (needed once per Python env):

```bash
uv tool run --from ipycli python -m ipykernel install --user
```

Verify:

```bash
ipycli --help
```

If you'd rather not install globally, run everything via `uv run` from a
checkout instead (`uv sync` once, then prefix commands with `uv run`).

### Enabling the agent skill

This repo ships a [`SKILL.md`](SKILL.md) that teaches coding agents (e.g.
Claude Code) how to drive `ipycli`. To make it available:

```bash
mkdir -p ~/.claude/skills/ipycli
cp SKILL.md ~/.claude/skills/ipycli/SKILL.md
```

Or, to scope it to a single project instead, copy it to
`<project>/.claude/skills/ipycli/SKILL.md`. The skill only takes effect once
`ipycli` itself is on `PATH` (see Install above) — it documents the CLI, it
doesn't install it.

## Commands

| Command | Purpose |
|---|---|
| `list-kernelspecs` | List installed kernel specs |
| `launch-kernel [SPEC]` | Launch a detached kernel (default `python3`); prints a unique id |
| `list-kernels` | List running kernels |
| `execute-code [-c CODE \| FILE] [-k ID] [-t SECS] [-b]` | Execute code from `-c`, a `.py`/`.ipynb` file, or stdin; `-b`/`--background` runs it detached and returns a job id immediately |
| `poll-background [-k ID] [-j JOB] [--wait] [--keep]` | Monitor and reap jobs started with `execute-code --background` |
| `list-variables [-k ID]` | List variables defined in the kernel |
| `restart-kernel [-k ID]` | Restart the kernel and clear history |
| `stop-kernel [-k ID]` | Stop the kernel |
| `show-history [-k ID]` | Show run code blocks with their ids and outputs |
| `export-history OUT.ipynb --ids a,b [-k ID]` | Export selected blocks to a notebook |

`--kernel-id`/`-k` may be omitted when exactly one kernel is running.

## Example

```bash
KID=$(ipycli launch-kernel python3 | jq -r .kernel_id)
ipycli execute-code -c "x = 21 * 2; print(x)"
ipycli execute-code -c "import matplotlib.pyplot as plt; plt.plot([1,2,3]); plt.show()"
ipycli list-variables
ipycli show-history
ipycli export-history out.ipynb --ids <id1>,<id2>
ipycli stop-kernel
```

For long-running code, run it in the background and poll for completion
instead of blocking the CLI invocation:

```bash
JOB=$(ipycli execute-code -c "long_running_job()" --background | jq -r .job_id)
ipycli poll-background --job-id $JOB --wait
```

Only one execution (foreground or background) runs per kernel at a time; a
second `execute-code` call is refused while a background job is still
in flight, until it's reaped with `poll-background`.

Plots are captured automatically: any figure a block produces is saved as a PNG
under `~/.ipycli/kernels/<id>/outputs/` and its path is returned in the block's
result.
