"""Resolve the user-configured external transcript command (the ``:transcript`` hook).

``:transcript`` runs a shell command of the user's choosing against the selected video
(e.g. ``summarize {url}`` or ``yeet {url} | fabric -sp summarize``). The placeholder
substitution (``build_command``) and shell runner (``run_transcript_command``) are exactly
the same as the general custom-command registry, so they live in ``custom_command`` and are
re-exported here — this module keeps only the transcript-specific *precedence* resolution.
Keeping one implementation is deliberate: hand-copied command logic is what the roadmap's
guiding theme warns against.
"""

import os
from typing import Optional

# Re-exported so existing callers (app.py, tests) keep importing these from here, while the
# implementation is shared with core.custom_command (no duplicate to diverge).
from .custom_command import build_command, run_command as run_transcript_command  # noqa: F401

ENV_VAR = "YANGER_TRANSCRIPT_COMMAND"


def resolve_transcript_command(settings, runtime_override: Optional[str] = None) -> Optional[str]:
    """Resolve the transcript command template by precedence.

    Highest first: ``runtime_override`` (from ``:set``) > env
    ``YANGER_TRANSCRIPT_COMMAND`` > YAML ``transcripts.transcript_command`` > None.

    Env is re-read here (not just at settings-load time) so resolution is correct
    even for a ``Settings`` built directly, e.g. in tests.
    """
    if runtime_override:
        return runtime_override
    env_cmd = os.environ.get(ENV_VAR)
    if env_cmd:
        return env_cmd
    # Accept either a full Settings (has .transcripts) or a TranscriptSettings.
    transcripts = getattr(settings, "transcripts", settings)
    yaml_cmd = getattr(transcripts, "transcript_command", "") or ""
    return yaml_cmd or None
