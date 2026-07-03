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
import logging
from dataclasses import dataclass
from typing import Dict

logger = logging.getLogger(__name__)

# Command execution modes.
MODE_PER_VIDEO = "per-video"
MODE_BATCH = "batch"
_VALID_MODES = frozenset({MODE_PER_VIDEO, MODE_BATCH})

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


def build_batch_command(template: str, videos) -> str:
    """Substitute ``{urls}`` / ``{ids}`` for a SINGLE batch invocation over a selection.

    Each url/id is ``shlex.quote``d and the set is space-joined (argv style, for tools like
    ``xargs``/``fabric``). If the template references neither placeholder, the quoted URLs are
    appended. Argv-only (no stdin in this slice); a very large selection can exceed ARG_MAX, so
    ``:run`` confirms before running a big batch.
    """
    urls = " ".join(shlex.quote(YOUTUBE_WATCH_URL.format(id=v.id)) for v in videos)
    ids = " ".join(shlex.quote(v.id) for v in videos)

    has_placeholder = "{urls}" in template or "{ids}" in template
    cmd = template.replace("{urls}", urls).replace("{ids}", ids)
    if not has_placeholder:
        cmd = f"{cmd} {urls}"
    return cmd


def run_command(cmd: str) -> int:
    """Run a resolved command via the shell and return its exit code.

    ``shell=True`` is required so user pipelines (``... | fabric -sp summarize``) work; only
    the injected url/id are quoted — the rest is the user's own configuration.
    """
    return subprocess.run(cmd, shell=True).returncode


@dataclass
class CommandSpec:
    """One named custom command.

    Bare string config ⇒ ``{template, mode=per-video, confirm=False}``. Long-form config
    (``{run, mode, confirm}``) sets ``mode`` (``per-video`` runs once per selected video;
    ``batch`` runs once with ``{urls}``/``{ids}``) and ``confirm`` (always prompt before running).
    """
    name: str      # normalized (lowercase) registry key
    template: str  # the shell template, with optional {url}/{id} (or {urls}/{ids}) placeholders
    mode: str = MODE_PER_VIDEO
    confirm: bool = False


def load_command_registry(settings) -> Dict[str, CommandSpec]:
    """Build the ``{name: CommandSpec}`` registry from settings + environment.

    Sources, lowest→highest precedence: ``settings.commands`` (a ``{name: template}`` map
    loaded from ``config.yaml``) then ``YANGER_CMD_<NAME>`` env vars. Names are normalized
    to lowercase so ``:run DL``, YAML ``dl:``, and ``YANGER_CMD_DL`` all address the same
    command. Blank/non-string templates are ignored.
    """
    registry: Dict[str, CommandSpec] = {}

    raw = getattr(settings, "commands", None) or {}
    for name, spec in raw.items():
        key = str(name).strip().lower()
        if not key:
            continue
        if isinstance(spec, str):
            if spec.strip():
                registry[key] = CommandSpec(name=key, template=spec)
        elif isinstance(spec, dict):
            template = spec.get("run", "")
            if not (isinstance(template, str) and template.strip()):
                logger.warning(f"custom command '{key}': long-form requires a non-empty 'run' string; skipped")
            else:
                mode = str(spec.get("mode", MODE_PER_VIDEO)).strip().lower()
                if mode not in _VALID_MODES:
                    logger.warning(
                        f"custom command '{key}': unknown mode '{mode}' (use per-video|batch); "
                        f"defaulting to {MODE_PER_VIDEO}"
                    )
                    mode = MODE_PER_VIDEO
                registry[key] = CommandSpec(
                    name=key, template=template, mode=mode,
                    confirm=bool(spec.get("confirm", False)),
                )

    # Env overrides / additions (runtime only; never persisted).
    for env_key, value in os.environ.items():
        if env_key.startswith(ENV_PREFIX) and isinstance(value, str) and value.strip():
            key = env_key[len(ENV_PREFIX):].strip().lower()
            if key:
                registry[key] = CommandSpec(name=key, template=value)

    return registry
