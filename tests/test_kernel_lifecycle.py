"""Kernel lifecycle: list-kernelspecs, launch/list/restart/stop-kernel,
and kernel-id resolution errors shared across commands.
"""

import json


def test_list_kernelspecs_includes_python3(run):
    proc, data = run.json("list-kernelspecs")
    assert proc.returncode == 0, proc.stderr
    names = [s["name"] for s in data]
    assert "python3" in names


def test_launch_kernel_default_spec_is_python3(run):
    proc, data = run.json("launch-kernel")
    assert proc.returncode == 0, proc.stderr
    assert data["spec"] == "python3"
    assert data["language"] == "python"
    assert data["pid"] > 0
    run("stop-kernel", "-k", data["kernel_id"])


def test_launch_kernel_unknown_spec_errors(run):
    proc, data = run.json("launch-kernel", "no-such-kernel-spec")
    assert proc.returncode != 0
    assert "no-such-kernel-spec" in data["error"]


def test_launch_kernel_bad_cwd_errors(run):
    proc, data = run.json("launch-kernel", "python3", "--cwd", "/no/such/directory")
    assert proc.returncode != 0
    assert "/no/such/directory" in data["error"]


def test_list_kernels_empty_when_none_running(run):
    proc, data = run.json("list-kernels")
    assert proc.returncode == 0, proc.stderr
    assert data == []


def test_list_kernels_shows_launched_kernel(run, kernel):
    proc, data = run.json("list-kernels")
    assert proc.returncode == 0, proc.stderr
    ids = [k["kernel_id"] for k in data]
    assert kernel in ids


def test_stop_kernel_removes_it_from_registry(run):
    proc, data = run.json("launch-kernel", "python3")
    kid = data["kernel_id"]

    proc, data = run.json("stop-kernel", "-k", kid)
    assert proc.returncode == 0, proc.stderr
    assert data["kernel_id"] == kid

    proc, data = run.json("list-kernels")
    assert kid not in [k["kernel_id"] for k in data]


def test_stop_kernel_twice_errors_second_time(run):
    proc, data = run.json("launch-kernel", "python3")
    kid = data["kernel_id"]
    run("stop-kernel", "-k", kid)

    proc, data = run.json("stop-kernel", "-k", kid)
    assert proc.returncode != 0
    assert kid in data["error"]


def test_restart_kernel_clears_history_and_gets_new_pid(run, kernel):
    proc, _ = run.json("execute-code", "-c", "leftover = 1", "-k", kernel)
    assert proc.returncode == 0, proc.stderr

    proc, before = run.json("list-kernels")
    old_pid = next(k["pid"] for k in before if k["kernel_id"] == kernel)

    proc, data = run.json("restart-kernel", "-k", kernel)
    assert proc.returncode == 0, proc.stderr
    assert data["kernel_id"] == kernel

    proc, after = run.json("list-kernels")
    new_pid = next(k["pid"] for k in after if k["kernel_id"] == kernel)
    assert new_pid != old_pid

    proc, hist = run.json("show-history", "-k", kernel)
    assert hist["blocks"] == []


def test_resolve_kernel_no_running_kernels_errors(run):
    proc, data = run.json("show-history")
    assert proc.returncode != 0
    assert "No running kernels" in data["error"]


def test_resolve_kernel_unknown_id_errors(run):
    proc, data = run.json("show-history", "-k", "deadbeef")
    assert proc.returncode != 0
    assert "deadbeef" in data["error"]


def test_resolve_kernel_ambiguous_without_id_errors(run):
    proc, a = run.json("launch-kernel", "python3")
    proc, b = run.json("launch-kernel", "python3")
    kid_a, kid_b = a["kernel_id"], b["kernel_id"]
    try:
        proc, data = run.json("show-history")
        assert proc.returncode != 0
        assert "Multiple kernels running" in data["error"]
        assert kid_a in data["error"] and kid_b in data["error"]

        # Each is individually addressable despite the ambiguity.
        proc, data = run.json("show-history", "-k", kid_a)
        assert proc.returncode == 0, proc.stderr
    finally:
        run("stop-kernel", "-k", kid_a)
        run("stop-kernel", "-k", kid_b)


def test_plain_output_for_list_kernels(run, kernel):
    proc = run("list-kernels", "--plain")
    assert proc.returncode == 0, proc.stderr
    with_prefix_ok = proc.stdout.strip() == "No running kernels." or kernel in proc.stdout
    assert with_prefix_ok
    # Plain output must not be JSON.
    try:
        json.loads(proc.stdout)
    except json.JSONDecodeError:
        pass
    else:
        raise AssertionError("--plain output should not be JSON")
