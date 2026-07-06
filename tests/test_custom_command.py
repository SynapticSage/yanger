"""Headline core slice: the user-defined custom-command registry (v1).

Covers the pure/testable core — placeholder substitution, the shell runner, registry
loading from settings + env, the Settings plumbing (from_dict/merge/save round-trip), and
the fact that core/transcript_command now delegates to this module (single implementation).
The TUI `:run` wiring is a separate slice.
"""

from types import SimpleNamespace

import pytest
import yaml

from yanger.core import custom_command
from yanger.core.custom_command import (
    build_command,
    build_batch_command,
    run_command,
    load_command_registry,
    CommandSpec,
    MODE_PER_VIDEO,
    MODE_BATCH,
)
from yanger.config.settings import Settings, save_settings


def _video(vid="dQw4w9WgXcQ"):
    return SimpleNamespace(id=vid)


# ----- build_command --------------------------------------------------------------

def test_build_command_substitutes_url_and_id():
    cmd = build_command("dl {url} --id {id}", _video("abc123"))
    assert "https://www.youtube.com/watch?v=abc123" in cmd
    assert "--id abc123" in cmd


def test_build_command_appends_url_when_no_placeholder():
    cmd = build_command("archivebox add", _video("abc123"))
    assert cmd.startswith("archivebox add ")
    assert "watch?v=abc123" in cmd


def test_build_command_shell_quotes_injection():
    # A well-formed id has no shell metacharacters, but the value is still quoted.
    cmd = build_command("tool {id}", _video("a b"))  # space forces quoting
    assert "'a b'" in cmd


# ----- run_command ----------------------------------------------------------------

def test_run_command_returns_subprocess_returncode(monkeypatch):
    monkeypatch.setattr(custom_command.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=7))
    assert run_command("anything") == 7


def test_run_command_uses_shell(monkeypatch):
    seen = {}
    monkeypatch.setattr(custom_command.subprocess, "run",
                        lambda cmd, **k: seen.update(cmd=cmd, shell=k.get("shell")) or SimpleNamespace(returncode=0))
    run_command("a | b")
    assert seen["shell"] is True and seen["cmd"] == "a | b"


# ----- load_command_registry ------------------------------------------------------

def test_registry_from_settings_bare_strings():
    settings = SimpleNamespace(commands={"dl": "yt-dlp {url}", "sum": "yeet {url} | fabric -sp summarize"})
    reg = load_command_registry(settings)
    assert set(reg) == {"dl", "sum"}
    assert reg["dl"] == CommandSpec(name="dl", template="yt-dlp {url}")


def test_registry_normalizes_and_skips_blank():
    settings = SimpleNamespace(commands={"DL": "yt-dlp {url}", "blank": "   "})
    reg = load_command_registry(settings)
    assert "dl" in reg and "DL" not in reg  # lowercased
    assert "blank" not in reg               # blank template ignored


def test_registry_env_overrides_and_adds(monkeypatch):
    monkeypatch.setenv("YANGER_CMD_DL", "custom-dl {url}")   # overrides yaml 'dl'
    monkeypatch.setenv("YANGER_CMD_ARCHIVE", "archivebox add {url}")  # new command
    settings = SimpleNamespace(commands={"dl": "yt-dlp {url}"})
    reg = load_command_registry(settings)
    assert reg["dl"].template == "custom-dl {url}"
    assert reg["archive"].template == "archivebox add {url}"


def test_registry_missing_commands_attr_is_empty():
    assert load_command_registry(SimpleNamespace()) == {}


# ----- slice 2: long-form specs + batch --------------------------------------------

def test_registry_parses_long_form():
    settings = SimpleNamespace(commands={
        "dlall": {"run": "yt-dlp {urls}", "mode": "batch", "confirm": True},
        "sum": {"run": "yeet {url}"},  # dict without mode/confirm -> defaults
    })
    reg = load_command_registry(settings)
    assert reg["dlall"] == CommandSpec(name="dlall", template="yt-dlp {urls}", mode=MODE_BATCH, confirm=True)
    assert reg["sum"].mode == MODE_PER_VIDEO and reg["sum"].confirm is False


def test_registry_invalid_mode_defaults_per_video():
    reg = load_command_registry(SimpleNamespace(commands={"x": {"run": "c {url}", "mode": "bacth"}}))
    assert reg["x"].mode == MODE_PER_VIDEO


def test_registry_long_form_without_run_is_skipped():
    reg = load_command_registry(SimpleNamespace(commands={"bad": {"mode": "batch"}}))
    assert "bad" not in reg


def test_build_batch_command_substitutes_urls_and_ids():
    vids = [SimpleNamespace(id="a"), SimpleNamespace(id="b b")]  # space forces quoting
    assert "watch?v=a" in build_batch_command("dl {urls}", vids)
    assert "'b b'" in build_batch_command("tool {ids}", vids)


def test_build_batch_command_appends_urls_when_no_placeholder():
    cmd = build_batch_command("archivebox add", [SimpleNamespace(id="a")])
    assert cmd.startswith("archivebox add ") and "watch?v=a" in cmd


# ----- Settings plumbing (all four touch-points) ----------------------------------

def test_settings_from_dict_parses_commands_lowercased_and_skips_nonstring():
    s = Settings.from_dict({"commands": {"DL": "yt-dlp {url}", "bad": 123}})
    assert s.commands == {"dl": "yt-dlp {url}"}  # non-string 'bad' skipped, key lowercased


def test_settings_merge_adds_commands():
    base = Settings.from_dict({"commands": {"a": "cmd-a"}})
    other = Settings.from_dict({"commands": {"b": "cmd-b"}})
    base.merge(other)
    assert base.commands == {"a": "cmd-a", "b": "cmd-b"}


def test_settings_save_roundtrips_commands(tmp_path):
    s = Settings.from_dict({"commands": {"dl": "yt-dlp {url}"}})
    save_settings(s, config_dir=tmp_path)
    written = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert written["commands"] == {"dl": "yt-dlp {url}"}
    # And it loads back.
    assert Settings.from_dict(written).commands == {"dl": "yt-dlp {url}"}


def test_settings_accepts_and_roundtrips_long_form(tmp_path):
    s = Settings.from_dict({"commands": {"dlall": {"run": "yt-dlp {urls}", "mode": "batch"}}})
    assert s.commands["dlall"] == {"run": "yt-dlp {urls}", "mode": "batch"}
    save_settings(s, config_dir=tmp_path)
    written = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert written["commands"]["dlall"]["mode"] == "batch"


# ----- transcript_command delegation (dedup) --------------------------------------

def test_transcript_command_delegates_to_custom_command():
    from yanger.core import transcript_command
    # Same function object -> single implementation, no hand-copied duplicate.
    assert transcript_command.build_command is build_command
    assert transcript_command.run_transcript_command is run_command
