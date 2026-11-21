"""Headline core slice: the user-defined custom-command registry (v1).

Covers the pure/testable core — placeholder substitution, the shell runner, registry
loading from settings + env, the Settings plumbing (from_dict/merge/save round-trip), and
the fact that core/transcript_command now delegates to this module (single implementation).
The TUI `:run` wiring is a separate slice.
"""

import os
import shlex
from types import SimpleNamespace

import mcp.types as t
import pytest
import yaml

from yanger.core import custom_command
from yanger.core.custom_command import (
    build_command,
    build_batch_command,
    run_command,
    resolve_commands,
    load_command_registry,
    build_stdin,
    run_command_capture,
    safe_title,
    CommandSpec,
    MODE_PER_VIDEO,
    MODE_BATCH,
    TRANSCRIPT_COMMAND_NAME,
    shadowing_env_var,
)
from yanger.config.settings import Settings, MCPSettings, save_settings


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


# ----- H.6: transcript_command as the registry's reserved `transcript` command ------

def test_registry_default_call_never_injects_transcript(monkeypatch):
    """MCP's call (no transcript_template) is unchanged even with the env hook set."""
    monkeypatch.setenv("YANGER_TRANSCRIPT_COMMAND", "summarize {url}")
    for k in [k for k in os.environ if k.startswith("YANGER_CMD_")]:
        monkeypatch.delenv(k)
    assert load_command_registry(SimpleNamespace(commands={})) == {}


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_registry_ignores_blank_transcript_template(monkeypatch, blank):
    """A whitespace-only transcript_command is unset, like every other blank template."""
    monkeypatch.delenv("YANGER_CMD_TRANSCRIPT", raising=False)
    reg = load_command_registry(SimpleNamespace(commands={}), transcript_template=blank)
    assert "transcript" not in reg


def test_registry_injects_transcript_template(monkeypatch):
    monkeypatch.delenv("YANGER_CMD_TRANSCRIPT", raising=False)
    reg = load_command_registry(SimpleNamespace(commands={"dl": "yt-dlp {url}"}),
                                transcript_template="t {url}")
    assert TRANSCRIPT_COMMAND_NAME == "transcript"
    assert reg["transcript"] == CommandSpec(name="transcript", template="t {url}")
    assert reg["transcript"].mode == MODE_PER_VIDEO and reg["transcript"].confirm is False
    assert reg["dl"].template == "yt-dlp {url}"


def test_registry_explicit_transcript_entry_wins_and_keeps_confirm(monkeypatch, caplog):
    """Back-compat: a user's own `commands.transcript` (with its confirm gate) is never replaced."""
    monkeypatch.delenv("YANGER_CMD_TRANSCRIPT", raising=False)
    settings = SimpleNamespace(commands={"transcript": {"run": "mine {url}", "confirm": True}})
    with caplog.at_level("WARNING"):
        reg = load_command_registry(settings, transcript_template="t {url}")
    assert reg["transcript"].template == "mine {url}" and reg["transcript"].confirm is True
    # The mismatch is warned by `:run transcript` only, not on every registry build.
    assert caplog.text == ""


def test_registry_env_transcript_entry_wins(monkeypatch, caplog):
    monkeypatch.setenv("YANGER_CMD_TRANSCRIPT", "envt {url}")
    with caplog.at_level("WARNING"):
        reg = load_command_registry(SimpleNamespace(commands={}), transcript_template="t {url}")
    assert reg["transcript"].template == "envt {url}"
    assert caplog.text == ""


def test_registry_same_template_no_warning(monkeypatch, caplog):
    monkeypatch.delenv("YANGER_CMD_TRANSCRIPT", raising=False)
    with caplog.at_level("WARNING"):
        load_command_registry(SimpleNamespace(commands={"transcript": "t {url}"}),
                              transcript_template="t {url}")
    assert caplog.text == ""


def test_registry_no_transcript_template_keeps_user_entry(monkeypatch):
    monkeypatch.delenv("YANGER_CMD_TRANSCRIPT", raising=False)
    reg = load_command_registry(SimpleNamespace(commands={"transcript": "mine {url}"}),
                                transcript_template=None)
    assert reg["transcript"].template == "mine {url}"


def test_shadowing_env_var_matches_case_insensitively(monkeypatch):
    for k in [k for k in os.environ if k.startswith("YANGER_CMD_")]:
        monkeypatch.delenv(k)
    assert shadowing_env_var("dl") is None
    monkeypatch.setenv("YANGER_CMD_Dl", "x {url}")
    assert shadowing_env_var("dl") == "YANGER_CMD_Dl"
    monkeypatch.setenv("YANGER_CMD_Dl", "   ")   # a blank value is ignored by the registry too
    assert shadowing_env_var("dl") is None


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


# --- resolve_commands: the single owner of the mode->builder dispatch (shared TUI + MCP) ---

def test_resolve_commands_per_video_one_pair_each():
    spec = CommandSpec(name="dl", template="echo {id}", mode=MODE_PER_VIDEO)
    runs = resolve_commands(spec, [_video("aaa"), _video("bbb")])
    assert [ids for ids, _cmd in runs] == [["aaa"], ["bbb"]]      # one run per video
    assert all("echo " in cmd for _ids, cmd in runs)


def test_resolve_commands_batch_single_pair_over_all():
    spec = CommandSpec(name="dlall", template="echo {ids}", mode=MODE_BATCH)
    runs = resolve_commands(spec, [_video("aaa"), _video("bbb")])
    assert len(runs) == 1                                          # ONE run for the selection
    ids, cmd = runs[0]
    assert ids == ["aaa", "bbb"]
    assert "aaa" in cmd and "bbb" in cmd


# ===== H.7 (batch stdin) + H.8 ({title}) ===========================================
# {title} is uploader-controlled, so it is SANITIZED to one word (letters/marks/digits + `_.,-`,
# no leading `-`, never `.`/`..`) and then shlex-quoted. Both features are TUI-only: resolve_commands
# refuses them unless the caller passes interactive=True (the MCP tool never does).

VID = "dQw4w9WgXcQ"


def _tvideo(title, vid=VID):
    return SimpleNamespace(id=vid, title=title)


@pytest.mark.parametrize("raw, expected", [
    ("Rick Astley - Never Gonna (Official) [4K]", "Rick_Astley_-_Never_Gonna__Official___4K_"),
    ("東京の夜 ナイト", "東京の夜_ナイト"),
    ("हिन्दी गाना", "हिन्दी_गाना"),          # Devanagari incl. combining marks (category M)
    ("-rf", "_rf"),
    ("a --exec=x", "a_--exec_x"),        # one word, so only a LEADING '-' could be an option
    ("$(x)", "__x_"),
    ("`x`", "_x_"),
    ("a'b\"c", "a_b_c"),
    ("a\nb\tc", "a_b_c"),
    ("v1.2, final", "v1.2,_final"),
    (".", "_"),                          # an all-dots title is no path component (traversal)
    ("..", "__"),
    ("...", "___"),
    ("..x", "..x"),
])
def test_safe_title_table(raw, expected):
    assert safe_title(raw) == expected


def test_safe_title_ascii_output_is_only_the_allowlist():
    out = safe_title("".join(chr(c) for c in range(128)))
    assert set(out) <= set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.,-")
    assert not out.startswith("-")


HOSTILE_TITLES = [
    "$(touch PWNED)",
    "`touch PWNED`",
    '"; touch PWNED; "',
    "'; touch PWNED; '",
    "a\ntouch PWNED",
    "x --exec=touch PWNED",
    "${IFS}touch${IFS}PWNED",
    "a|touch PWNED",
    "a&&touch PWNED",
]
TITLE_TEMPLATES = [
    "printf %s {title}",
    'printf %s "{title}"',
    "printf %s '{title}'",
    'printf %s "x{title}y"',
    'sh -c "printf %s {title}"',
    "sh -c 'printf %s {title}'",
    "eval printf %s {title}",
    "printf '%s\\n' {title} | xargs printf %s",
]


@pytest.mark.parametrize("template", TITLE_TEMPLATES)
@pytest.mark.parametrize("title", HOSTILE_TITLES)
def test_title_injection_proof_real_shell(tmp_path, monkeypatch, template, title):
    """H.8 acceptance: no hostile title executes, in ANY quote context, under a real /bin/sh."""
    monkeypatch.chdir(tmp_path)
    spec = CommandSpec(name="t", template=template)
    [(_ids, cmd)] = resolve_commands(spec, [_tvideo(title)], interactive=True)
    assert not safe_title(title).startswith("-")   # one word, no option injection either
    run_command_capture(cmd, timeout=10)
    assert not (tmp_path / "PWNED").exists(), cmd


@pytest.mark.parametrize("template", [
    "set -- {title}; printf %s $#",
    'set -- "{title}"; printf %s $#',
    "set -- '{title}'; printf %s $#",
    'set -- "x{title}y"; printf %s $#',
    "eval set -- {title}; printf %s $#",
])
@pytest.mark.parametrize("title", ["x important.txt", "a b  c", "a\tb\nc"])
def test_title_is_exactly_one_argument_in_any_quote_context(template, title):
    """Review finding 0: a spaced title must not word-split into extra positional arguments."""
    [(_ids, cmd)] = resolve_commands(
        CommandSpec(name="t", template=template), [_tvideo(title)], interactive=True)
    res = run_command_capture(cmd, timeout=10)
    assert res.stdout == "1", cmd


def test_title_injection_control_shlex_quote_alone_is_bypassable(tmp_path, monkeypatch):
    """CONTROL: proves the harness detects injection, and why shlex.quote alone is not enough."""
    monkeypatch.chdir(tmp_path)
    run_command_capture('printf %s "' + shlex.quote("$(touch PWNED)") + '"', timeout=10)
    assert (tmp_path / "PWNED").exists()


def test_title_only_template_does_not_append_url():
    [(_ids, cmd)] = resolve_commands(
        CommandSpec(name="t", template="echo {title}"), [_tvideo("My Video")], interactive=True)
    assert cmd == "echo My_Video"


def test_build_command_without_allow_title_leaves_title_literal():
    # :transcript back-compat: byte-for-byte the old behaviour.
    cmd = build_command("echo {title}", _tvideo("My Video", vid="abc123"))
    assert cmd == "echo {title} 'https://www.youtube.com/watch?v=abc123'"


@pytest.mark.parametrize("spec", [
    CommandSpec(name="t", template="echo {title}"),
    CommandSpec(name="s", template="cat", mode=MODE_BATCH, stdin="urls"),
])
def test_non_interactive_resolve_refuses_title_and_stdin(spec):
    """The MCP call shape: default interactive=False, SimpleNamespace(id=...) stand-ins."""
    with pytest.raises(ValueError, match="TUI-only"):
        resolve_commands(spec, [SimpleNamespace(id=VID)])


@pytest.mark.parametrize("video", [_tvideo(""), _tvideo("   "), SimpleNamespace(id=VID)])
def test_interactive_title_refuses_missing_or_empty_title(video):
    with pytest.raises(ValueError, match=VID):
        resolve_commands(CommandSpec(name="t", template="echo {title}"), [video], interactive=True)


def test_interactive_empty_title_refuses_whole_run():
    spec = CommandSpec(name="t", template="echo {title}")
    with pytest.raises(ValueError, match="bbbbbbbbbbb"):
        resolve_commands(spec, [_tvideo("ok"), _tvideo("", vid="bbbbbbbbbbb")], interactive=True)


def test_interactive_batch_with_title_refused():
    spec = CommandSpec(name="t", template="echo {title}", mode=MODE_BATCH)
    with pytest.raises(ValueError, match="batch"):
        resolve_commands(spec, [_tvideo("x")], interactive=True)


def test_interactive_per_video_stdin_refused():
    spec = CommandSpec(name="t", template="cat", stdin="urls")
    with pytest.raises(ValueError, match="batch"):
        resolve_commands(spec, [_tvideo("x")], interactive=True)


def test_quoted_true_stdin_message_names_the_yaml_boolean():
    """Review finding 5: `stdin: "true"` (a string) must not be told to "use true"."""
    reg = load_command_registry(SimpleNamespace(commands={
        "x": {"run": "cat", "mode": "batch", "stdin": "true"}}))
    with pytest.raises(ValueError, match="unquoted"):
        resolve_commands(reg["x"], [_tvideo("x")], interactive=True)


def test_interactive_unknown_stdin_value_refused():
    spec = CommandSpec(name="t", template="cat", mode=MODE_BATCH, stdin="bogus")
    with pytest.raises(ValueError, match="bogus"):
        resolve_commands(spec, [_tvideo("x")], interactive=True)


@pytest.mark.parametrize("value, expected", [
    (True, "urls"), ("urls", "urls"), ("URLs", "urls"), ("ids", "ids"),
    (False, None), (None, None), ("bogus", "bogus"),
])
def test_registry_parses_stdin(value, expected):
    reg = load_command_registry(SimpleNamespace(commands={
        "x": {"run": "cat", "mode": "batch", "stdin": value}}))
    assert reg["x"].stdin == expected   # an unknown value is KEPT and refused at :run time


def test_registry_stdin_missing_bare_and_env_are_none(monkeypatch):
    monkeypatch.setenv("YANGER_CMD_ENVX", "cat")
    reg = load_command_registry(SimpleNamespace(commands={
        "bare": "cat", "long": {"run": "cat", "mode": "batch"}}))
    assert reg["bare"].stdin is None and reg["long"].stdin is None and reg["envx"].stdin is None


def test_batch_stdin_does_not_append_urls():
    spec = CommandSpec(name="s", template="xargs -n1 echo", mode=MODE_BATCH, stdin="urls")
    [(ids, cmd)] = resolve_commands(spec, [_tvideo("x", "a"), _tvideo("y", "b")], interactive=True)
    assert cmd == "xargs -n1 echo" and ids == ["a", "b"]


def test_build_stdin_payloads():
    urls = CommandSpec(name="s", template="cat", mode=MODE_BATCH, stdin="urls")
    ids = CommandSpec(name="s", template="cat", mode=MODE_BATCH, stdin="ids")
    assert build_stdin(urls, ["a", "b"]) == (
        "https://www.youtube.com/watch?v=a\nhttps://www.youtube.com/watch?v=b\n")
    assert build_stdin(ids, ["a", "b"]) == "a\nb\n"
    assert build_stdin(CommandSpec(name="n", template="cat"), ["a"]) is None


def test_run_command_with_stdin_passes_input_text(monkeypatch):
    seen = {}
    monkeypatch.setattr(custom_command.subprocess, "run",
                        lambda cmd, **k: seen.update(k) or SimpleNamespace(returncode=0))
    run_command("cat", stdin="a\n")
    assert seen == {"shell": True, "input": "a\n", "text": True}


def test_run_command_stdin_end_to_end(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert run_command("cat > out", stdin="a\nb\n") == 0
    assert (tmp_path / "out").read_text() == "a\nb\n"


def test_settings_roundtrips_stdin_key(tmp_path):
    s = Settings.from_dict({"commands": {"s": {"run": "cat", "mode": "batch", "stdin": True}}})
    save_settings(s, config_dir=tmp_path)
    written = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert written["commands"]["s"]["stdin"] is True
    assert load_command_registry(Settings.from_dict(written))["s"].stdin == "urls"


@pytest.mark.parametrize("command", ["t", "s"])
async def test_mcp_tool_refuses_title_and_stdin_commands(tmp_path, monkeypatch, command):
    """MCP non-exposure guard, driven only through the public call_tool handler: a {title} or
    stdin command is refused and NOTHING runs (each template would `touch RAN` first)."""
    from yanger.mcp_server import YangerMCPServer
    monkeypatch.chdir(tmp_path)
    s = YangerMCPServer()
    s._settings = Settings(mcp=MCPSettings(allow_custom_commands=True), commands={
        "t": "touch RAN; echo {title}",
        "s": {"run": "touch RAN; cat", "mode": "batch", "stdin": True},
    })
    handler = s.server.request_handlers[t.CallToolRequest]
    res = await handler(t.CallToolRequest(method="tools/call", params=t.CallToolRequestParams(
        name="run_custom_command", arguments={"command": command, "video_ids": [VID]})))
    # Only the refusal's meaning is pinned, not its error shape (owned by the mcp chain).
    assert "TUI-only" in res.root.content[0].text
    assert not (tmp_path / "RAN").exists()
