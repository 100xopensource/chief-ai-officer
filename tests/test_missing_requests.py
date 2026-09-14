"""A missing `requests` is a sentence, not a traceback.

Downloading is the only thing in this project that needs a third-party library,
which means it is the one dependency failure a real user actually meets — and
they meet it several steps in, on the first command that touches their own
account, where a traceback reads like the tool is broken rather than like it is
one `pip install` short.

These tests hide `requests` from the import system and assert on what comes out.
"""

from __future__ import annotations

import builtins
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[1] / "plugins" / "100x-chief-ai-officer"

FETCH_MODULES = [
    "pipeline.fetch.analytics",      # cost and usage
    "pipeline.fetch.compliance",     # conversation content, via compliance_api
    "pipeline.fetch.directory",      # seats, via compliance_api
]


@pytest.fixture
def no_requests(monkeypatch):
    """Make `import requests` fail the way it fails on a machine without it."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "requests" or name.startswith("requests."):
            raise ModuleNotFoundError("No module named 'requests'", name="requests")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.delitem(sys.modules, "requests", raising=False)


def test_names_the_library_the_fix_and_the_interpreter(no_requests, capsys):
    from pipeline.fetch._requests import require_requests

    with pytest.raises(SystemExit) as exit_info:
        require_requests()

    message = capsys.readouterr().err
    assert "requests" in message
    assert "pip install requests" in message
    # The interpreter is named because a plugin install and a `pip` on PATH are
    # routinely different ones, and installing into the wrong one looks like
    # success.
    assert sys.executable in message
    # Exit code, not a message payload: cli._run_fetch converts a fetch module's
    # SystemExit back into a return code, and a string there would raise.
    assert exit_info.value.code == 1
    assert isinstance(exit_info.value.code, int)


def test_says_nothing_was_written(no_requests, capsys):
    from pipeline.fetch._requests import require_requests

    with pytest.raises(SystemExit):
        require_requests()

    assert "Nothing was written" in capsys.readouterr().err


def test_a_dependency_of_requests_is_not_swallowed(monkeypatch):
    """Only `requests` itself gets the friendly message.

    If `requests` is installed but something it imports is broken, that is a
    different problem and must not be reported as "run pip install requests".
    """
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "requests":
            raise ModuleNotFoundError("No module named 'urllib3'", name="urllib3")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.delitem(sys.modules, "requests", raising=False)

    from pipeline.fetch._requests import require_requests

    with pytest.raises(ModuleNotFoundError) as exc_info:
        require_requests()
    assert exc_info.value.name == "urllib3"


def test_returns_the_real_module_when_it_is_there():
    from pipeline.fetch._requests import require_requests

    assert require_requests().__name__ == "requests"


@pytest.mark.parametrize("module", FETCH_MODULES)
def test_no_fetch_entry_point_tracebacks(module, tmp_path):
    """Every way in reports the same thing, including `python3 -m ...` directly.

    Run in a subprocess with `requests` blocked at the meta-path, because import
    failure is an import-time property and this test is about what a person sees
    on their terminal.
    """
    blocker = textwrap.dedent(f"""
        import sys
        class Blocker:
            def find_module(self, name, path=None):
                return None
            def find_spec(self, name, target=None, path=None):
                if name == "requests" or name.startswith("requests."):
                    raise ModuleNotFoundError("No module named 'requests'", name="requests")
                return None
        sys.meta_path.insert(0, Blocker())
        sys.argv = ["{module}", "--data-dir", {str(tmp_path)!r}]
        import runpy
        runpy.run_module("{module}", run_name="__main__")
    """)
    env = dict(os.environ, PYTHONPATH=str(PLUGIN_ROOT))
    result = subprocess.run([sys.executable, "-c", blocker], env=env,
                            capture_output=True, text=True, timeout=120)

    assert "Traceback" not in result.stderr, result.stderr
    assert "ModuleNotFoundError" not in result.stderr, result.stderr
    assert "pip install requests" in result.stderr
    assert result.returncode == 1
