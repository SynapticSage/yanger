"""Regression tests for the unified auth-path resolver.

The CLI (`yanger auth`), TUI and MCP server must all agree on where the OAuth
token/secret live, or `yanger auth` writes a token the MCP server can't find.
These tests pin the resolution order so that coherence can't silently regress.
"""
from pathlib import Path

from yanger.auth import (
    config_dir,
    resolve_token_file,
    resolve_client_secrets_file,
    YouTubeAuth,
)


def _patch_home(monkeypatch, home: Path):
    # Path.home is the same callable in every module, so patching it here is global.
    monkeypatch.setattr("yanger.auth.Path.home", classmethod(lambda cls: home))


def test_absolute_configured_path_wins(monkeypatch, tmp_path):
    explicit = tmp_path / "elsewhere" / "tok.json"
    assert resolve_token_file(str(explicit)) == explicit


def test_relative_configured_anchors_under_config_dir_not_cwd(monkeypatch, tmp_path):
    """A relative configured value must anchor under the config dir, never cwd."""
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)  # prove it ignores cwd
    assert resolve_token_file("mytok.json") == tmp_path / ".config" / "yanger" / "mytok.json"


def test_canonical_preferred_when_present(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    canonical = tmp_path / ".config" / "yanger" / "token.json"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("{}")
    assert resolve_token_file() == canonical


def test_legacy_cwd_fallback_when_canonical_absent(monkeypatch, tmp_path):
    """Existing ./token.json keeps working when no canonical token exists."""
    home = tmp_path / "home"
    work = tmp_path / "work"
    work.mkdir()
    _patch_home(monkeypatch, home)
    monkeypatch.chdir(work)
    legacy = work / "token.json"
    legacy.write_text("{}")
    assert resolve_token_file() == legacy


def test_default_write_location_is_canonical(monkeypatch, tmp_path):
    """With nothing present, the resolver points at the canonical write path."""
    home = tmp_path / "home"
    work = tmp_path / "work"
    work.mkdir()
    _patch_home(monkeypatch, home)
    monkeypatch.chdir(work)
    assert resolve_token_file() == home / ".config" / "yanger" / "token.json"


def test_client_secrets_legacy_config_fallback(monkeypatch, tmp_path):
    home = tmp_path / "home"
    work = tmp_path / "work"
    (work / "config").mkdir(parents=True)
    _patch_home(monkeypatch, home)
    monkeypatch.chdir(work)
    legacy = work / "config" / "client_secret.json"
    legacy.write_text("{}")
    assert resolve_client_secrets_file() == legacy


def test_youtubeauth_uses_resolver(monkeypatch, tmp_path):
    _patch_home(monkeypatch, tmp_path)
    canonical = tmp_path / ".config" / "yanger" / "token.json"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("{}")
    auth = YouTubeAuth()
    assert auth.token_file == str(canonical)
    assert Path(auth.client_secrets_file).is_absolute()
