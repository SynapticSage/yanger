"""Tier-0 hardening tests: `yanger reset` path resolution + cache PRAGMAs (0.3, 0.5, 0.6).

`reset` previously targeted repo-relative paths (`./.yanger_cache`, `config/*.yaml`) that
never exist for a real install, so it silently no-op'd. These tests pin it to the REAL
resolvers by sandboxing $HOME, and confirm the destructive actions are guarded.
"""

from pathlib import Path

from click.testing import CliRunner

from yanger.cli import cli
from yanger.cache import PersistentCache, default_cache_dir


def _sandbox_home(monkeypatch, tmp_path):
    """Point $HOME at a temp dir; the path resolvers read it at call time."""
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


# ----- 0.3 reset targets the real paths -----------------------------------------

def test_reset_cache_removes_real_cache_dir(monkeypatch, tmp_path):
    home = _sandbox_home(monkeypatch, tmp_path)
    cache_dir = default_cache_dir()
    assert cache_dir == home / ".cache" / "yanger"
    cache_dir.mkdir(parents=True)
    (cache_dir / "cache.db").write_text("x")

    result = CliRunner().invoke(cli, ["reset", "--reset-cache", "--yes"])

    assert result.exit_code == 0, result.output
    assert not cache_dir.exists()


def test_reset_config_removes_real_config_file(monkeypatch, tmp_path):
    home = _sandbox_home(monkeypatch, tmp_path)
    config_file = home / ".config" / "yanger" / "config.yaml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("ui: {}\n")

    result = CliRunner().invoke(cli, ["reset", "--reset-config", "--yes"])

    assert result.exit_code == 0, result.output
    assert not config_file.exists()


def test_reset_token_removes_canonical_token(monkeypatch, tmp_path):
    home = _sandbox_home(monkeypatch, tmp_path)
    token = home / ".config" / "yanger" / "token.json"
    token.parent.mkdir(parents=True)
    token.write_text("{}")

    result = CliRunner().invoke(cli, ["reset", "--reset-token", "--yes"])

    assert result.exit_code == 0, result.output
    assert not token.exists()


def test_reset_requires_confirmation_without_yes(monkeypatch, tmp_path):
    """Without --yes and answering 'n', the cache must survive (destructive-op guard)."""
    home = _sandbox_home(monkeypatch, tmp_path)
    cache_dir = default_cache_dir()
    cache_dir.mkdir(parents=True)

    result = CliRunner().invoke(cli, ["reset", "--reset-cache"], input="n\n")

    assert result.exit_code == 0, result.output
    assert cache_dir.exists()  # declined → not deleted


def test_reset_missing_paths_reports_not_found(monkeypatch, tmp_path):
    _sandbox_home(monkeypatch, tmp_path)
    result = CliRunner().invoke(cli, ["reset", "--reset-cache", "--yes"])
    assert result.exit_code == 0
    assert "No cache directory found" in result.output


# ----- 0.6 verbose flag is accepted at the group level --------------------------

def test_verbose_flag_accepted(monkeypatch, tmp_path):
    _sandbox_home(monkeypatch, tmp_path)
    result = CliRunner().invoke(cli, ["--verbose", "reset"])
    assert result.exit_code == 0, result.output
    assert "Nothing to reset" in result.output


# ----- 0.5 cache connection PRAGMAs ---------------------------------------------

def test_connect_sets_wal_and_busy_timeout(tmp_path):
    cache = PersistentCache(cache_dir=tmp_path / "c", auto_cleanup=False)
    conn = cache._connect()
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()
