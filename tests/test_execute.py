"""execute-code, list-variables, show-history: the core run/inspect loop.

Most tests share one kernel (module-scoped) purely to avoid paying real
kernel-startup cost per test; each test uses uniquely named variables/markers
so they don't depend on each other's state.
"""

import json

import nbformat


def test_execute_code_returns_result(shared_run, shared_kernel):
    proc, data = shared_run.json("execute-code", "-c", "1 + 1", "-k", shared_kernel)
    assert proc.returncode == 0, proc.stderr
    assert data["status"] == "ok"
    assert data["execution_count"] >= 1
    assert data["block_id"]
    assert any(o["type"] == "execute_result" and o["text"] == "2" for o in data["outputs"])


def test_execute_code_stdout_stream(shared_run, shared_kernel):
    proc, data = shared_run.json(
        "execute-code", "-c", "print('hello ipycli')", "-k", shared_kernel
    )
    assert proc.returncode == 0, proc.stderr
    stream_texts = [o["text"] for o in data["outputs"] if o["type"] == "stream"]
    assert any("hello ipycli" in t for t in stream_texts)


def test_execute_code_error_status(shared_run, shared_kernel):
    proc, data = shared_run.json(
        "execute-code", "-c", "raise ValueError('boom')", "-k", shared_kernel
    )
    assert proc.returncode == 0, proc.stderr  # execution errors aren't CLI failures
    assert data["status"] == "error"
    err = next(o for o in data["outputs"] if o["type"] == "error")
    assert err["ename"] == "ValueError"
    assert err["evalue"] == "boom"


def test_execute_code_from_py_file(shared_run, shared_kernel, tmp_path):
    f = tmp_path / "script.py"
    f.write_text("py_file_marker = 41 + 1\nprint(py_file_marker)\n")
    proc, data = shared_run.json("execute-code", str(f), "-k", shared_kernel)
    assert proc.returncode == 0, proc.stderr
    assert data["status"] == "ok"
    assert any("42" in o.get("text", "") for o in data["outputs"])


def test_execute_code_from_ipynb_runs_each_cell(shared_run, shared_kernel, tmp_path):
    nb = nbformat.v4.new_notebook()
    nb.cells = [
        nbformat.v4.new_code_cell("nb_cell_marker_a = 10"),
        nbformat.v4.new_code_cell("print(nb_cell_marker_a + 5)"),
    ]
    f = tmp_path / "in.ipynb"
    nbformat.write(nb, str(f))

    proc, data = shared_run.json("execute-code", str(f), "-k", shared_kernel)
    assert proc.returncode == 0, proc.stderr
    assert len(data["blocks"]) == 2
    assert data["blocks"][0]["status"] == "ok"
    assert any("15" in o.get("text", "") for o in data["blocks"][1]["outputs"])


def test_execute_code_missing_file_errors(shared_run, shared_kernel):
    proc, data = shared_run.json(
        "execute-code", "/no/such/file.py", "-k", shared_kernel
    )
    assert proc.returncode != 0
    assert "/no/such/file.py" in data["error"]


def test_execute_code_reads_stdin_when_no_code_or_file(shared_run, shared_kernel):
    proc, data = shared_run.json(
        "execute-code", "-k", shared_kernel, input="stdin_marker = 7\nprint(stdin_marker)\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert any("7" in o.get("text", "") for o in data["outputs"])


def test_execute_code_empty_stdin_errors(shared_run, shared_kernel):
    proc, data = shared_run.json("execute-code", "-k", shared_kernel, input="   \n")
    assert proc.returncode != 0
    assert "empty input" in data["error"].lower()


def test_execute_code_captures_matplotlib_plot(shared_run, shared_kernel):
    code = (
        "import matplotlib.pyplot as plt\n"
        "plt.plot([1, 2, 3])\n"
        "plt.show()\n"
    )
    proc, data = shared_run.json("execute-code", "-c", code, "-k", shared_kernel)
    assert proc.returncode == 0, proc.stderr
    assert data["status"] == "ok"
    assert len(data["plots"]) == 1
    from pathlib import Path

    assert Path(data["plots"][0]).is_file()


def test_list_variables_reports_defined_variable(shared_run, shared_kernel):
    proc, _ = shared_run.json(
        "execute-code", "-c", "list_vars_marker = {'a': 1}", "-k", shared_kernel
    )
    assert proc.returncode == 0, proc.stderr

    proc, data = shared_run.json("list-variables", "-k", shared_kernel)
    assert proc.returncode == 0, proc.stderr
    entry = next(v for v in data if v["name"] == "list_vars_marker")
    assert entry["type"] == "dict"


def test_show_history_includes_executed_block(shared_run, shared_kernel):
    proc, exec_data = shared_run.json(
        "execute-code", "-c", "history_marker = 'xyz'", "-k", shared_kernel
    )
    bid = exec_data["block_id"]

    proc, data = shared_run.json("show-history", "-k", shared_kernel)
    assert proc.returncode == 0, proc.stderr
    block = next(b for b in data["blocks"] if b["block_id"] == bid)
    assert block["code"] == "history_marker = 'xyz'"
    assert block["status"] == "ok"


def test_plain_output_for_execute_code(shared_run, shared_kernel):
    proc = shared_run("execute-code", "-c", "40 + 2", "-k", shared_kernel, "--plain")
    assert proc.returncode == 0, proc.stderr
    assert "status=ok" in proc.stdout
    assert "=> 42" in proc.stdout
    try:
        json.loads(proc.stdout)
    except json.JSONDecodeError:
        pass
    else:
        raise AssertionError("--plain output should not be JSON")


def test_execute_code_timeout_status(run, kernel):
    proc, data = run.json(
        "execute-code", "-c", "import time; time.sleep(5)",
        "-k", kernel, "--timeout", "1",
    )
    assert proc.returncode == 0, proc.stderr
    assert data["status"] == "timeout"

    # Kernel is interrupted but still usable for the next block.
    proc, data = run.json("execute-code", "-c", "1 + 1", "-k", kernel)
    assert proc.returncode == 0, proc.stderr
    assert data["status"] == "ok"
