"""export-history: selecting history blocks by id and writing them to a notebook."""

import nbformat


def test_export_history_selected_ids(run, kernel, tmp_path):
    proc, a = run.json("execute-code", "-c", "export_marker_a = 1", "-k", kernel)
    proc, b = run.json("execute-code", "-c", "export_marker_b = 2", "-k", kernel)

    out = tmp_path / "out.ipynb"
    proc, data = run.json(
        "export-history", str(out), "--ids", a["block_id"], "-k", kernel
    )
    assert proc.returncode == 0, proc.stderr
    assert data["cells"] == 1
    assert out.is_file()

    nb = nbformat.read(str(out), as_version=4)
    assert len(nb.cells) == 1
    assert nb.cells[0].source == "export_marker_a = 1"
    assert nb.metadata["kernelspec"]["name"] == "python3"


def test_export_history_multiple_ids_comma_separated(run, kernel, tmp_path):
    proc, a = run.json("execute-code", "-c", "multi_marker_a = 1", "-k", kernel)
    proc, b = run.json("execute-code", "-c", "multi_marker_b = 2", "-k", kernel)

    out = tmp_path / "multi.ipynb"
    ids = f"{a['block_id']},{b['block_id']}"
    proc, data = run.json("export-history", str(out), "--ids", ids, "-k", kernel)
    assert proc.returncode == 0, proc.stderr
    assert data["cells"] == 2

    nb = nbformat.read(str(out), as_version=4)
    sources = [c.source for c in nb.cells]
    assert sources == ["multi_marker_a = 1", "multi_marker_b = 2"]


def test_export_history_defaults_to_all_blocks(run, kernel, tmp_path):
    run.json("execute-code", "-c", "all_marker_a = 1", "-k", kernel)
    run.json("execute-code", "-c", "all_marker_b = 2", "-k", kernel)

    out = tmp_path / "all.ipynb"
    proc, data = run.json("export-history", str(out), "-k", kernel)
    assert proc.returncode == 0, proc.stderr
    assert data["cells"] == 2


def test_export_history_unknown_id_errors(run, kernel, tmp_path):
    out = tmp_path / "bad.ipynb"
    proc, data = run.json(
        "export-history", str(out), "--ids", "not-a-real-id", "-k", kernel
    )
    assert proc.returncode != 0
    assert "not-a-real-id" in data["error"]
    assert not out.exists()


def test_export_history_captures_plot_as_display_data(run, kernel, tmp_path):
    code = "import matplotlib.pyplot as plt\nplt.plot([1, 2])\nplt.show()\n"
    proc, data = run.json("execute-code", "-c", code, "-k", kernel)
    assert data["plots"]

    out = tmp_path / "plot.ipynb"
    proc, result = run.json(
        "export-history", str(out), "--ids", data["block_id"], "-k", kernel
    )
    assert proc.returncode == 0, proc.stderr

    nb = nbformat.read(str(out), as_version=4)
    outputs = nb.cells[0].outputs
    assert any(
        o.get("output_type") == "display_data" and "image/png" in o.get("data", {})
        for o in outputs
    )
