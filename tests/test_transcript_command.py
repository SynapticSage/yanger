"""Tests for the configurable :transcript external-command hook.

Covers placeholder substitution / shell-quoting in build_command and the
precedence in resolve_transcript_command. These never spawn a subprocess.
"""

import shlex
from dataclasses import dataclass

import pytest

from yanger.core.transcript_command import (
    ENV_VAR,
    build_command,
    resolve_transcript_command,
)
from yanger.config.settings import Settings


@dataclass
class FakeVideo:
    """Minimal stand-in for models.Video (build_command only needs .id)."""
    id: str


VID = "dQw4w9WgXcQ"
URL = f"https://www.youtube.com/watch?v={VID}"


class TestBuildCommand:
    def test_url_placeholder_substituted_and_quoted(self):
        cmd = build_command("summarize {url}", FakeVideo(VID))
        assert cmd == f"summarize {shlex.quote(URL)}"

    def test_id_placeholder_substituted_and_quoted(self):
        cmd = build_command("fetch {id}", FakeVideo(VID))
        assert cmd == f"fetch {shlex.quote(VID)}"

    def test_url_appended_when_no_placeholder(self):
        cmd = build_command("summarize", FakeVideo(VID))
        assert cmd == f"summarize {shlex.quote(URL)}"

    def test_pipeline_preserved_with_url(self):
        cmd = build_command("yeet {url} | fabric -sp summarize", FakeVideo(VID))
        assert cmd == f"yeet {shlex.quote(URL)} | fabric -sp summarize"
        # The pipe stays unquoted (it's the user's own config)...
        assert "| fabric -sp summarize" in cmd

    def test_both_placeholders(self):
        cmd = build_command("tool {id} {url}", FakeVideo(VID))
        assert cmd == f"tool {shlex.quote(VID)} {shlex.quote(URL)}"

    def test_injection_is_neutralised_by_quoting(self):
        # A hostile-looking id must be shell-quoted, not interpreted.
        evil = "x; rm -rf ~"
        cmd = build_command("summarize {id}", FakeVideo(evil))
        assert cmd == f"summarize {shlex.quote(evil)}"
        assert "; rm -rf" not in cmd.replace(shlex.quote(evil), "")


class TestResolvePrecedence:
    def _settings(self, yaml_value=""):
        s = Settings()
        s.transcripts.transcript_command = yaml_value
        return s

    def test_runtime_override_wins(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "env-cmd {url}")
        s = self._settings("yaml-cmd {url}")
        assert resolve_transcript_command(s, runtime_override="run {url}") == "run {url}"

    def test_env_beats_settings(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "env-cmd {url}")
        s = self._settings("yaml-cmd {url}")
        assert resolve_transcript_command(s) == "env-cmd {url}"

    def test_settings_used_when_no_env(self, monkeypatch):
        monkeypatch.delenv(ENV_VAR, raising=False)
        s = self._settings("yaml-cmd {url}")
        assert resolve_transcript_command(s) == "yaml-cmd {url}"

    def test_none_when_unset(self, monkeypatch):
        monkeypatch.delenv(ENV_VAR, raising=False)
        s = self._settings("")
        assert resolve_transcript_command(s) is None

    def test_accepts_transcript_settings_directly(self, monkeypatch):
        # resolve should also work when handed a TranscriptSettings, not Settings.
        monkeypatch.delenv(ENV_VAR, raising=False)
        s = self._settings("yaml-cmd {url}")
        assert resolve_transcript_command(s.transcripts) == "yaml-cmd {url}"
