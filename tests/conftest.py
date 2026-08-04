"""Shared fixtures for ipycli's end-to-end tests.

Every test drives the real ``ipycli`` CLI as a subprocess against an isolated
``IPYCLI_HOME`` (a fresh tmp dir), so tests never touch a developer's actual
``~/.ipycli`` state and can run concurrently with real kernels.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

CLI = [sys.executable, "-m", "ipycli"]
DEFAULT_TIMEOUT = 30


class Runner:
    """Invokes the ipycli CLI as a subprocess against an isolated IPYCLI_HOME."""

    def __init__(self, home: Path):
        self.home = home

    def __call__(self, *args, input=None, timeout=DEFAULT_TIMEOUT):
        env = {**os.environ, "IPYCLI_HOME": str(self.home)}
        return subprocess.run(
            [*CLI, *args],
            env=env,
            input=input,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def json(self, *args, input=None, timeout=DEFAULT_TIMEOUT):
        """Run and parse JSON from stdout (success) or stderr (failure)."""
        proc = self(*args, input=input, timeout=timeout)
        stream = proc.stdout if proc.returncode == 0 else proc.stderr
        data = json.loads(stream) if stream.strip() else None
        return proc, data


def _launch(run: Runner, spec: str = "python3") -> str:
    proc, data = run.json("launch-kernel", spec)
    assert proc.returncode == 0, proc.stderr
    return data["kernel_id"]


def _stop(run: Runner, kid: str) -> None:
    run("stop-kernel", "-k", kid)


@pytest.fixture
def run(tmp_path):
    """A Runner over a fresh, private IPYCLI_HOME for this test only."""
    return Runner(tmp_path)


@pytest.fixture
def kernel(run):
    """A freshly launched python3 kernel, stopped on teardown."""
    kid = _launch(run)
    yield kid
    _stop(run, kid)


@pytest.fixture(scope="module")
def shared_run(tmp_path_factory):
    """A Runner over a IPYCLI_HOME shared by every test in the module.

    Used by read-mostly tests that don't need lifecycle isolation, so we don't
    pay kernel-startup cost per test.
    """
    return Runner(tmp_path_factory.mktemp("ipycli-home"))


@pytest.fixture(scope="module")
def shared_kernel(shared_run):
    kid = _launch(shared_run)
    yield kid
    _stop(shared_run, kid)
