#!/usr/bin/env python3
"""The post-install welcome: what it says, and when it says it.

Three things are worth guarding here, and each has a plain failure story.

**It must not depend on the package being installed.** The hook's whole purpose
is to speak before anybody has run `pip install`, so a stray import of pandas —
or of a stage module that imports pandas — would break it in exactly the
situation it exists for, and only on somebody else's machine.

**It must describe this machine, not a machine.** Telling a person to run `caio
demo` when `caio` is not installed, or to open `docs/mock-reports/` when there
is no checkout on disk, wastes the first ten minutes. That is the failure this
project keeps finding in its own documentation.

**It must speak once.** A greeting on every session start is an advert.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugins" / "100x-chief-ai-officer"
HOOK = PLUGIN / "hooks" / "session_start.py"

sys.path.insert(0, str(PLUGIN))

from pipeline import welcome  # noqa: E402
from pipeline.render.validate import JARGON_PATTERNS  # noqa: E402


# --------------------------------------------------------------------------
# Fixtures that fake a machine

def fake_checkout(root: Path) -> Path:
    """The two things `find_repo_root` insists on, and nothing else."""
    (root / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    (root / "plugins" / "100x-chief-ai-officer").mkdir(parents=True)
    return root


def fake_lake(root: Path, name: str) -> None:
    """A lake is recognised by its state file, not by the folder existing."""
    lake = root / name
    lake.mkdir(parents=True, exist_ok=True)
    (lake / welcome.LAKE_MARKER).write_text("{}", encoding="utf-8")


# --------------------------------------------------------------------------
# What it says

def test_says_what_the_three_reports_are():
    text = welcome.message(welcome.detect(REPO))
    for name in ("Waste Ledger", "Value X-Ray", "Exposure Report"):
        assert name in text
    for reader in ("Finance", "Compliance"):
        assert reader in text


def test_says_that_nothing_has_happened_yet():
    """The reasonable fear on installing this is that it has already read
    everything. Saying so is the point, not a nicety."""
    text = welcome.message(welcome.detect(REPO))
    assert "Nothing has happened yet" in text
    assert "nothing is uploaded" in text.lower()


def test_offers_exactly_the_two_next_steps_the_ticket_asks_for():
    text = welcome.message(welcome.detect(REPO))
    assert "Look at a finished report" in text
    assert "Get set up with your own data" in text
    # Two, numbered, and no third.
    assert re.search(r"^### 1\.", text, re.M)
    assert re.search(r"^### 2\.", text, re.M)
    assert not re.search(r"^### 3\.", text, re.M)


def test_no_pipeline_jargon_reaches_the_reader():
    """The same rule the reports are held to. A person who has just installed
    this knows nothing about stages, lakes, husks or candidates."""
    text = welcome.message(welcome.detect(REPO))
    tripped = [why for pattern, why in JARGON_PATTERNS if re.search(pattern, text, re.I)]
    assert not tripped, f"welcome text trips the jargon gate: {tripped}"


def test_it_explains_both_keys_and_that_either_works_alone():
    text = welcome.message(welcome.detect(REPO))
    assert "Admin API key" in text
    assert "Compliance API key" in text
    assert "Either works on its own" in text


# --------------------------------------------------------------------------
# It describes this machine

def test_outside_a_checkout_it_points_at_the_repository(tmp_path, monkeypatch):
    monkeypatch.setattr(welcome.shutil, "which", lambda _name: None)
    state = welcome.detect(tmp_path)
    assert state["repo_root"] is None
    assert state["examples_dir"] is None

    text = welcome.message(state)
    assert welcome.EXAMPLES_URL in text
    assert "git clone" in text
    # Never tell somebody to open a folder that is not there.
    assert "docs/mock-reports/`" not in text


def test_inside_a_checkout_it_points_at_the_folder_on_disk():
    state = welcome.detect(REPO)
    assert state["repo_root"] == str(REPO)
    assert state["examples_dir"] is not None

    text = welcome.message(state)
    assert "docs/mock-reports" in text
    assert "git clone" not in text


def test_uninstalled_checkout_offers_install_then_the_installed_commands(tmp_path, monkeypatch):
    """The old bug this guards: printing `pip install -e .` and then, on the
    next line, the form you use when you have *not* installed."""
    monkeypatch.setattr(welcome.shutil, "which", lambda _name: None)
    text = welcome.message(welcome.detect(fake_checkout(tmp_path)))
    assert "pip install -e ." in text
    assert "caio demo --out data-demo" in text
    # The no-install alternative is offered as prose, not mixed into the block.
    block = text.split("```bash")[1].split("```")[0]
    assert "PYTHONPATH" not in block


def test_installed_commands_carry_no_prefix(tmp_path, monkeypatch):
    monkeypatch.setattr(welcome.shutil, "which", lambda _name: "/usr/local/bin/caio")
    text = welcome.message(welcome.detect(fake_checkout(tmp_path)))
    assert "caio demo --out data-demo" in text
    assert "pip install" not in text
    assert "PYTHONPATH" not in text


def test_a_virtualenv_that_is_not_on_the_path_still_counts_as_installed(tmp_path, monkeypatch):
    """Found by installing it in a venv and running it: `caio` worked, PATH did
    not mention it, and the greeting told the reader to install it again."""
    monkeypatch.setattr(welcome.shutil, "which", lambda _name: None)
    monkeypatch.setattr(welcome.sys, "argv", ["/somewhere/.venv/bin/caio", "welcome"])
    text = welcome.message(welcome.detect(fake_checkout(tmp_path)))
    assert "pip install" not in text
    assert "caio demo --out data-demo" in text


def test_an_existing_lake_is_noticed_rather_than_duplicated(tmp_path, monkeypatch):
    monkeypatch.setattr(welcome.shutil, "which", lambda _name: "/usr/local/bin/caio")
    fake_lake(tmp_path, "data-demo")
    state = welcome.detect(tmp_path)
    assert state["lakes"] == ["data-demo"]
    assert "already data pulled here" in welcome.message(state)


def test_an_empty_data_folder_is_not_a_lake(tmp_path):
    (tmp_path / "data").mkdir()
    assert welcome.detect(tmp_path)["lakes"] == []


def test_reports_already_built_are_named(tmp_path):
    reports = tmp_path / "_reports"
    reports.mkdir()
    (reports / "waste-ledger-2026-07-12.html").write_text("<html></html>", encoding="utf-8")
    state = welcome.detect(tmp_path)
    assert state["reports"] == ["waste-ledger-2026-07-12.html"]
    assert "waste-ledger-2026-07-12.html" in welcome.message(state)


# --------------------------------------------------------------------------
# The brief handed to Claude

def test_brief_tells_claude_to_offer_two_things_and_run_nothing():
    brief = welcome.brief_for_claude(welcome.detect(REPO))
    assert "plain English" in brief
    assert "example report" in brief
    assert "Do not run anything" in brief
    assert "caio-setup" in brief


def test_brief_states_whether_the_command_is_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(welcome.shutil, "which", lambda _name: None)
    assert "not installed" in welcome.brief_for_claude(welcome.detect(tmp_path))

    monkeypatch.setattr(welcome.shutil, "which", lambda _name: "/usr/local/bin/caio")
    assert "on PATH" in welcome.brief_for_claude(welcome.detect(tmp_path))


# --------------------------------------------------------------------------
# The hook

def run_hook(tmp_path: Path, **env: str) -> subprocess.CompletedProcess:
    """Run the hook the way Claude Code does: no PYTHONPATH, nothing installed.

    `PYTHONPATH` is stripped deliberately. If the hook only works because the
    test runner happened to put the package on the path, it does not work.
    """
    environment = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "CAIO_WELCOME")}
    environment.update({
        "CLAUDE_PLUGIN_ROOT": str(PLUGIN),
        "CLAUDE_PLUGIN_DATA": str(tmp_path / "plugin-data"),
        "CLAUDE_PROJECT_DIR": str(tmp_path / "project"),
        **env,
    })
    (tmp_path / "project").mkdir(exist_ok=True)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"hook_event_name": "SessionStart",
                          "session_start_reason": "startup"}),
        capture_output=True, text=True, env=environment, timeout=30,
    )


def test_hook_speaks_once_and_then_stays_quiet(tmp_path):
    first = run_hook(tmp_path)
    assert first.returncode == 0
    payload = json.loads(first.stdout)
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "three reports" in payload["hookSpecificOutput"]["additionalContext"]
    assert payload["systemMessage"]

    second = run_hook(tmp_path)
    assert second.returncode == 0
    assert second.stdout.strip() == ""


def test_hook_can_be_silenced_and_can_be_forced(tmp_path):
    assert run_hook(tmp_path, CAIO_WELCOME="never").stdout.strip() == ""

    forced = tmp_path / "forced"
    forced.mkdir()
    for _ in range(2):
        assert json.loads(run_hook(forced, CAIO_WELCOME="always").stdout)["systemMessage"]


def test_hook_never_fails_the_session(tmp_path):
    """A greeting that can stop somebody starting work is worse than none."""
    broken = run_hook(tmp_path, CLAUDE_PLUGIN_ROOT=str(tmp_path / "nowhere"))
    assert broken.returncode == 0


def test_hook_says_nothing_has_been_pulled_when_nothing_has(tmp_path):
    context = json.loads(run_hook(tmp_path).stdout)["hookSpecificOutput"]["additionalContext"]
    assert "no data has been pulled" in context


def test_hook_json_is_declared_and_points_at_a_script_that_exists():
    """A malformed hooks.json fails silently: the plugin installs, the hook
    never runs, and nobody finds out."""
    config = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    entries = config["hooks"]["SessionStart"]
    assert entries, "no SessionStart hook declared"
    for group in entries:
        for hook in group["hooks"]:
            assert hook["type"] == "command"
            named = hook["command"].replace("${CLAUDE_PLUGIN_ROOT}", str(PLUGIN))
            target = Path(named.split('"')[1] if '"' in named else named.split()[-1])
            assert target.is_file(), f"hook command points at nothing: {target}"


@pytest.mark.parametrize("module", ["pandas", "requests"])
def test_welcome_pulls_in_no_third_party_dependency(module, tmp_path):
    """Imported in a subprocess so the test runner's own imports cannot mask it."""
    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(PLUGIN)!r})\n"
        "from pipeline import welcome\n"
        "welcome.message(welcome.detect())\n"
        f"assert {module!r} not in sys.modules, {module!r} + ' was imported'\n"
    )
    environment = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    done = subprocess.run([sys.executable, "-c", script], capture_output=True,
                          text=True, cwd=tmp_path, env=environment, timeout=30)
    assert done.returncode == 0, done.stderr


# --------------------------------------------------------------------------
# installed as a plugin, and nothing else
#
# The case the first version got wrong. "Not a checkout" was read as "must be
# installed", so somebody who installed from the marketplace and never ran pip
# was told to type `caio` — which they do not have. The first thing the
# documentation told them to do did not work.
# --------------------------------------------------------------------------

def test_a_plugin_only_install_is_not_told_to_type_caio(tmp_path, monkeypatch):
    plugin = tmp_path / "plugin_abc123"
    (plugin / "pipeline").mkdir(parents=True)
    (plugin / "pipeline" / "cli.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin))
    monkeypatch.setattr(welcome, "command_is_available", lambda: False)

    state = welcome.detect(tmp_path)
    assert state["plugin_root"] == str(plugin)
    assert state["installed"] is False
    assert state["repo_root"] is None

    line = welcome._run(state, "check --data-dir data")
    assert line.startswith("python3 ")
    assert "cli.py" in line
    assert not line.startswith("caio ")


def test_the_plugin_path_is_a_variable_not_a_resolved_path(tmp_path, monkeypatch):
    """That directory is session-scoped: it changes between sessions. A path
    resolved into the instructions is correct exactly once and then silently
    wrong, which is worse than no path at all."""
    plugin = tmp_path / "local-agent-mode-sessions" / "abc" / "plugin_1"
    (plugin / "pipeline").mkdir(parents=True)
    (plugin / "pipeline" / "cli.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin))
    monkeypatch.setattr(welcome, "command_is_available", lambda: False)

    state = welcome.detect(tmp_path)
    text = welcome.message(state) + welcome.brief_for_claude(state)
    assert "$CLAUDE_PLUGIN_ROOT" in text
    assert str(plugin) not in text, (
        "the session-scoped path was written into the instructions; it will be "
        "wrong the next time anyone reads them"
    )


def test_a_stale_plugin_root_is_not_offered(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path / "gone"))
    monkeypatch.setattr(welcome, "command_is_available", lambda: False)
    assert welcome.detect(tmp_path)["plugin_root"] is None


def test_the_brief_explains_that_a_mid_session_install_needs_a_restart():
    """ListPlugins comes back empty and the skill errors although the plugin is
    on disk. It reads as a failed install; the fix is a new conversation, and
    nothing says so."""
    brief = welcome.brief_for_claude(welcome.detect()).lower()
    assert "start a new conversation" in brief
    assert "not a failed install" in brief


def test_one_key_is_enough_and_the_page_says_so():
    """The table reads as "I need both", and somebody holding a single
    Compliance Access Key decides they are blocked when they are not."""
    text = welcome.message(welcome.detect())
    assert "Either works on its own" in text
    assert "Two keys are supported, not required" in text


def test_the_next_step_a_plugin_run_prints_is_one_that_runs(monkeypatch, tmp_path):
    """Every command prints a next step. In a marketplace install that step was
    `python3 -m pipeline.cli`, which fails there — no checkout, nothing on
    PYTHONPATH. It was handed to the reader immediately after a command that had
    just worked, which is the exact shape of failure this project keeps finding
    in its own instructions."""
    from pipeline import cli

    plugin = tmp_path / "plugin_xyz"
    (plugin / "pipeline").mkdir(parents=True)
    script = plugin / "pipeline" / "cli.py"
    script.write_text("", encoding="utf-8")

    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin))
    monkeypatch.setattr(cli.sys, "argv", [str(script), "demo"])
    assert cli.invoked_as() == 'python3 "$CLAUDE_PLUGIN_ROOT/pipeline/cli.py"'

    # Run from a path that is not the declared plugin root: name the real file
    # rather than a variable that points somewhere else.
    other = tmp_path / "elsewhere" / "pipeline"
    other.mkdir(parents=True)
    (other / "cli.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(cli.sys, "argv", [str(other / "cli.py"), "demo"])
    assert cli.invoked_as() == f'python3 "{other / "cli.py"}"'


def test_the_installed_and_checkout_forms_are_unchanged(monkeypatch):
    from pipeline import cli
    monkeypatch.setattr(cli.sys, "argv", ["/usr/local/bin/caio", "demo"])
    assert cli.invoked_as() == "caio"
    monkeypatch.setattr(cli.sys, "argv", ["-m", "demo"])
    assert cli.invoked_as() == "python3 -m pipeline.cli"


def test_help_names_the_command_the_reader_typed(monkeypatch, capsys):
    """`caio --help` used to answer `usage: pipeline.cli`."""
    from pipeline import cli
    monkeypatch.setattr(cli.sys, "argv", ["/usr/local/bin/caio"])
    assert cli.build_parser().format_usage().startswith("usage: caio")
