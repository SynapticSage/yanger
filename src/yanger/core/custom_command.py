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

``{title}`` (per-video) is uploader-controlled, so it is SANITIZED by ``safe_title`` to one
inert shell word before it is quoted, which keeps it a single argument even inside quotes. It
must sit in argument position, never as the command (``eval {title}``, ``sh -c {title}``).
Batch ``stdin`` pipes newline-joined URLs/ids into the command. Both are TUI-only: ``resolve_commands`` refuses them unless the
caller passes ``interactive=True`` (the MCP tool does not).
"""

import os
import re
import shlex
import subprocess
import logging
import unicodedata
from dataclasses import dataclass
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Command execution modes.
MODE_PER_VIDEO = "per-video"
MODE_BATCH = "batch"
_VALID_MODES = frozenset({MODE_PER_VIDEO, MODE_BATCH})

# {id} -> raw YouTube video id; expands to the standard watch URL for {url}.
YOUTUBE_WATCH_URL = "https://www.youtube.com/watch?v={id}"

# Env override convention, mirroring YANGER_TRANSCRIPT_COMMAND: YANGER_CMD_DL -> command "dl".
ENV_PREFIX = "YANGER_CMD_"

# H.6: the registry name under which the TUI offers `transcripts.transcript_command`
# (`:run transcript`). An explicit `commands.transcript` / YANGER_CMD_TRANSCRIPT keeps it.
TRANSCRIPT_COMMAND_NAME = "transcript"

# Batch stdin payloads (CommandSpec.stdin).
STDIN_URLS = "urls"
STDIN_IDS = "ids"

_TITLE_EXTRA = frozenset("_.,-")


def safe_title(title: str) -> str:
    """Reduce an uploader-controlled title to characters that are inert to the shell.

    Every character that is not a letter, mark or digit (unicodedata L*/M*/N*) and not one of
    ``_.,-`` becomes ``_`` (spaces included); a leading ``-`` becomes ``_``; an all-dots result
    (``.``, ``..``) becomes underscores.

    Invariant: the output is ONE shell word with no ASCII whitespace, shell metacharacter, quote,
    ``$``, backtick, backslash, glob, ``=`` or ``/``; it does not start with ``-`` and is not
    ``.``/``..``. So in ARGUMENT position it stays exactly one inert argument in every quote
    context and under re-parsing (``sh -c "..."``, ``eval``, ``xargs``): no word splitting, no
    option injection, no path traversal. It is NOT safe as the command word itself (``eval
    {title}`` would run a title like ``reboot``); templates must not put it there.
    """
    s = "".join(
        c if c in _TITLE_EXTRA or unicodedata.category(c)[0] in "LMN" else "_" for c in title
    )
    if set(s) == {"."}:
        return "_" * len(s)
    return re.sub(r"^-", "_", s)


def build_command(template: str, video, *, allow_title: bool = False) -> str:
    """Substitute shlex-quoted ``{url}`` / ``{id}`` placeholders into a shell template.

    If the template references NEITHER placeholder, the quoted URL is appended so a bare
    command like ``yt-dlp`` still receives the video. Shared by ``:run`` and the
    ``:transcript`` hook.

    With ``allow_title`` (only ``:run`` sets it), ``{title}`` is a placeholder too and becomes
    ``shlex.quote(safe_title(video.title))``; a missing or blank title raises ValueError. Without
    it, ``{title}`` stays literal (the ``:transcript`` hook's unchanged behaviour).
    """
    url = YOUTUBE_WATCH_URL.format(id=video.id)
    quoted_url = shlex.quote(url)
    quoted_id = shlex.quote(video.id)

    use_title = allow_title and "{title}" in template
    has_placeholder = "{url}" in template or "{id}" in template or use_title
    cmd = template.replace("{url}", quoted_url).replace("{id}", quoted_id)
    if use_title:
        # getattr: pre-metadata Takeout videos have title "", and MCP's stand-ins have none.
        title = getattr(video, "title", None)
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"video {video.id} has no title (metadata not fetched?); "
                             "{title} needs one")
        cmd = cmd.replace("{title}", shlex.quote(safe_title(title)))
    if not has_placeholder:
        cmd = f"{cmd} {quoted_url}"
    return cmd


def build_batch_command(template: str, videos, *, append: bool = True) -> str:
    """Substitute ``{urls}`` / ``{ids}`` for a SINGLE batch invocation over a selection.

    Each url/id is ``shlex.quote``d and the set is space-joined (argv style, for tools like
    ``xargs``/``fabric``). If the template references neither placeholder, the quoted URLs are
    appended, unless ``append`` is False (a stdin command already receives them on stdin). A very
    large selection can exceed ARG_MAX, so ``:run`` confirms before running a big batch.
    """
    urls = " ".join(shlex.quote(YOUTUBE_WATCH_URL.format(id=v.id)) for v in videos)
    ids = " ".join(shlex.quote(v.id) for v in videos)

    has_placeholder = "{urls}" in template or "{ids}" in template
    cmd = template.replace("{urls}", urls).replace("{ids}", ids)
    if append and not has_placeholder:
        cmd = f"{cmd} {urls}"
    return cmd


def resolve_commands(
    spec: "CommandSpec", videos, *, interactive: bool = False
) -> "list[tuple[list[str], str]]":
    """Resolve a command spec + video selection into runnable ``(video_ids, shell_cmd)`` pairs.

    THE single owner of the mode→builder dispatch, so callers can't drift (the TUI's ``:run`` and
    the MCP ``run_custom_command`` tool both go through here):
    - ``batch``: ONE pair over the whole selection (``{urls}``/``{ids}``).
    - ``per-video``: one pair per video.
    Each ``shell_cmd`` already has its url/id ``shlex.quote``d by build_command/build_batch_command.
    ``video_ids`` is per-run attribution: MCP shapes its result rows by it; the TUI ignores it.

    ``{title}`` and ``stdin`` are TUI-only: without ``interactive=True`` a spec using either is
    refused (ValueError) before anything is built. Every command is built before returning, so a
    bad title refuses the whole run rather than part of it.
    """
    if not interactive:
        if spec.stdin is not None or "{title}" in spec.template:
            raise ValueError(f"command '{spec.name}' uses {{title}} or stdin, which is TUI-only "
                             "(:run); not available to non-interactive callers")
    elif spec.stdin is not None and spec.stdin not in (STDIN_URLS, STDIN_IDS):
        raise ValueError(f"unknown stdin value '{spec.stdin}' "
                         "(use YAML boolean true, unquoted, or urls or ids)")
    elif spec.stdin is not None and spec.mode != MODE_BATCH:
        raise ValueError("stdin needs mode: batch")
    elif spec.mode == MODE_BATCH and "{title}" in spec.template:
        raise ValueError("{title} is per-video only; it is not available in mode: batch")

    if spec.mode == MODE_BATCH:
        cmd = build_batch_command(spec.template, videos, append=spec.stdin is None)
        return [([v.id for v in videos], cmd)]
    return [([v.id], build_command(spec.template, v, allow_title=interactive)) for v in videos]


def build_stdin(spec: "CommandSpec", video_ids) -> Optional[str]:
    """The stdin payload for one run: newline-joined URLs or ids (trailing newline), or None."""
    if spec.stdin is None:
        return None
    if spec.stdin == STDIN_URLS:
        lines = [YOUTUBE_WATCH_URL.format(id=v) for v in video_ids]
    elif spec.stdin == STDIN_IDS:
        lines = list(video_ids)
    else:
        raise ValueError(f"unknown stdin value '{spec.stdin}'")
    return "".join(f"{line}\n" for line in lines)


def run_command(cmd: str, stdin: Optional[str] = None) -> int:
    """Run a resolved command via the shell and return its exit code.

    ``shell=True`` is required so user pipelines (``... | fabric -sp summarize``) work; only
    the injected url/id are quoted — the rest is the user's own configuration. With ``stdin``
    the command reads that text from a pipe instead of the terminal.
    """
    if stdin is None:
        return subprocess.run(cmd, shell=True).returncode
    return subprocess.run(cmd, shell=True, input=stdin, text=True).returncode


# MCP has no TTY, so it can't stream like the TUI's `:run`; a capturing runner is required to
# return output to the LLM. Bounded by a timeout because `asyncio.to_thread` cannot be cancelled
# — without it a hung user command (e.g. one waiting on a prompt) would pin an MCP worker forever.
DEFAULT_CAPTURE_TIMEOUT = 120  # seconds; mirrors the fabric-subprocess bound


@dataclass
class CommandResult:
    """Outcome of a captured command run. ``stdout``/``stderr`` may be partial on timeout."""
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


def run_command_capture(cmd: str, timeout: float = DEFAULT_CAPTURE_TIMEOUT) -> CommandResult:
    """Run a resolved command via the shell and CAPTURE its output (for MCP; the TUI streams).

    Only the delta from ``run_command`` (above) is capture/text/errors/timeout — same shell=True
    posture, same quoting contract. ``errors='replace'`` so non-UTF-8 tool output can't raise.
    On timeout the shell is killed and a partial result is returned with ``timed_out=True`` (note
    a pipeline's orphaned grandchildren may outlive the killed ``/bin/sh`` — a known shell=True limit).
    """
    try:
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            errors="replace", timeout=timeout,
        )
        return CommandResult(proc.returncode, proc.stdout or "", proc.stderr or "")
    except subprocess.TimeoutExpired as e:
        # e.stdout/e.stderr are bytes-or-str depending on platform; normalize to str.
        def _s(v) -> str:
            if v is None:
                return ""
            return v if isinstance(v, str) else v.decode("utf-8", "replace")
        return CommandResult(
            returncode=-1, stdout=_s(e.stdout), stderr=_s(e.stderr), timed_out=True,
        )


@dataclass
class CommandSpec:
    """One named custom command.

    Bare string config ⇒ ``{template, mode=per-video, confirm=False}``. Long-form config
    (``{run, mode, confirm}``) sets ``mode`` (``per-video`` runs once per selected video;
    ``batch`` runs once with ``{urls}``/``{ids}``) and ``confirm`` (always prompt before running).
    ``stdin`` (``true``/``urls`` or ``ids``; batch only) pipes the selection in, one per line.
    """
    name: str      # normalized (lowercase) registry key
    template: str  # the shell template, with optional {url}/{id}/{title} (or {urls}/{ids}) placeholders
    mode: str = MODE_PER_VIDEO
    confirm: bool = False
    # None, STDIN_URLS or STDIN_IDS. An unrecognised config value is kept as-is so `:run` can
    # name it when refusing (rather than the command silently vanishing from the registry).
    stdin: Optional[str] = None


def _parse_stdin(value) -> Optional[str]:
    """Normalize a long-form ``stdin:`` value: false/null/missing -> None, true -> urls."""
    if value is None or value is False:
        return None
    if value is True:
        return STDIN_URLS
    return str(value).strip().lower()


def shadowing_env_var(name: str) -> Optional[str]:
    """Return the ``YANGER_CMD_*`` env var that overrides registry command ``name``, if any."""
    for env_key, value in os.environ.items():
        if (env_key.startswith(ENV_PREFIX) and value.strip()
                and env_key[len(ENV_PREFIX):].strip().lower() == name):
            return env_key
    return None


def load_command_registry(
    settings, *, transcript_template: Optional[str] = None
) -> Dict[str, CommandSpec]:
    """Build the ``{name: CommandSpec}`` registry from settings + environment.

    Sources, lowest→highest precedence: ``transcript_template`` (H.6: the resolved
    ``transcript_command``, offered as ``transcript`` only if no other source defines that
    name), then ``settings.commands`` (a ``{name: template}`` map loaded from ``config.yaml``),
    then ``YANGER_CMD_<NAME>`` env vars. Names are normalized to lowercase so ``:run DL``,
    YAML ``dl:``, and ``YANGER_CMD_DL`` all address the same command. Blank/non-string
    templates are ignored. The default (no ``transcript_template``) is what MCP uses.
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
                    stdin=_parse_stdin(spec.get("stdin")),  # optional key
                )

    # Env overrides / additions (runtime only; never persisted).
    for env_key, value in os.environ.items():
        if env_key.startswith(ENV_PREFIX) and isinstance(value, str) and value.strip():
            key = env_key[len(ENV_PREFIX):].strip().lower()
            if key:
                registry[key] = CommandSpec(name=key, template=value)

    if transcript_template:
        # An explicit `transcript` entry wins (back-compat: its confirm gate is never dropped).
        existing = registry.setdefault(
            TRANSCRIPT_COMMAND_NAME,
            CommandSpec(name=TRANSCRIPT_COMMAND_NAME, template=transcript_template),
        )
        if existing.template != transcript_template:
            logger.warning(
                f"custom command '{TRANSCRIPT_COMMAND_NAME}' ({existing.template!r}) overrides "
                f"transcript_command ({transcript_template!r}) for :run; :transcript still runs the latter"
            )

    return registry
