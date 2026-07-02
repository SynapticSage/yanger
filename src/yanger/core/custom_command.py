"""User-defined custom-command registry: run shell commands on videos by name.

Generalizes the single ``:transcript`` hook into a *named registry*. Users map command
names to shell templates in ``commands:`` (``config.yaml``) or via ``YANGER_CMD_<NAME>``
env vars, then run them with ``:run <name>`` against the current video or a marked
selection.

This module owns the canonical placeholder substitution (``build_command``) and shell
runner (``run_command``); ``core/transcript_command.py`` delegates to them so there is a
single implementation (the roadmap's guiding theme is "no duplication on untested paths").

Safety posture (same as the transcript hook): the command runs with ``shell=True`` because
it is the user's own configuration and may contain pipes; only the injected ``{url}``/``{id}``
are ``shlex.quote``d. Placeholders must sit as standalone arguments, not inside quotes.
"""

import os
import shlex
import subprocess
from dataclasses import dataclass
from typing import Dict

# {id} -> raw YouTube video id; expands to the standard watch URL for {url}.
YOUTUBE_WATCH_URL = "https://www.youtube.com/watch?v={id}"

# Env override convention, mirroring YANGER_TRANSCRIPT_COMMAND: YANGER_CMD_DL -> command "dl".
ENV_PREFIX = "YANGER_CMD_"


def build_command(template: str, video) -> str:
    """Substitute shlex-quoted ``{url}`` / ``{id}`` placeholders into a shell template.

    If the template references NEITHER placeholder, the quoted URL is appended so a bare
    command like ``yt-dlp`` still receives the video. Shared by ``:run`` and the
    ``:transcript`` hook.
    """
    url = YOUTUBE_WATCH_URL.format(id=video.id)
    quoted_url = shlex.quote(url)
    quoted_id = shlex.quote(video.id)

    has_placeholder = "{url}" in template or "{id}" in template
    cmd = template.replace("{url}", quoted_url).replace("{id}", quoted_id)
    if not has_placeholder:
        cmd = f"{cmd} {quoted_url}"
    return cmd


def run_command(cmd: str) -> int:
    """Run a resolved command via the shell and return its exit code.

    ``shell=True`` is required so user pipelines (``... | fabric -sp summarize``) work; only
    the injected url/id are quoted — the rest is the user's own configuration.
    """
    return subprocess.run(cmd, shell=True).returncode


@dataclass
class CommandSpec:
    """One named custom command. v1 is a bare shell template (per-video)."""
    name: str      # normalized (lowercase) registry key
    template: str  # the shell template, with optional {url}/{id} placeholders


def load_command_registry(settings) -> Dict[str, CommandSpec]:
    """Build the ``{name: CommandSpec}`` registry from settings + environment.

    Sources, lowest→highest precedence: ``settings.commands`` (a ``{name: template}`` map
    loaded from ``config.yaml``) then ``YANGER_CMD_<NAME>`` env vars. Names are normalized
    to lowercase so ``:run DL``, YAML ``dl:``, and ``YANGER_CMD_DL`` all address the same
    command. Blank/non-string templates are ignored.
    """
    registry: Dict[str, CommandSpec] = {}

    raw = getattr(settings, "commands", None) or {}
    for name, template in raw.items():
        if isinstance(template, str) and template.strip():
            key = str(name).strip().lower()
            if key:
                registry[key] = CommandSpec(name=key, template=template)

    # Env overrides / additions (runtime only; never persisted).
    for env_key, value in os.environ.items():
        if env_key.startswith(ENV_PREFIX) and isinstance(value, str) and value.strip():
            key = env_key[len(ENV_PREFIX):].strip().lower()
            if key:
                registry[key] = CommandSpec(name=key, template=value)

    return registry
