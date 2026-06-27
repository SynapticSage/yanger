"""Build and run the user-configured external transcript command.

`:transcript` runs a shell command of the user's choosing against the selected
video (e.g. ``summarize {url}`` or ``yeet {url} | fabric -sp summarize``). Keeping
the substitution / resolution logic here keeps ``app.py`` thin and lets us
unit-test it without ever spawning a subprocess.
"""

import os
import shlex
import subprocess
from typing import Optional

# Placeholders supported in a transcript-command template.
YOUTUBE_WATCH_URL = "https://www.youtube.com/watch?v={id}"
ENV_VAR = "YANGER_TRANSCRIPT_COMMAND"


def build_command(template: str, video) -> str:
    """Substitute ``{url}`` / ``{id}`` placeholders in a transcript template.

    The url and id are shell-quoted before injection (the command runs with
    ``shell=True``). If the template references NEITHER placeholder, the quoted
    URL is appended so a bare command like ``summarize`` still gets the video.
    """
    url = YOUTUBE_WATCH_URL.format(id=video.id)
    quoted_url = shlex.quote(url)
    quoted_id = shlex.quote(video.id)

    has_placeholder = "{url}" in template or "{id}" in template
    cmd = template.replace("{url}", quoted_url).replace("{id}", quoted_id)
    if not has_placeholder:
        cmd = f"{cmd} {quoted_url}"
    return cmd


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


def run_transcript_command(cmd: str) -> int:
    """Run the resolved command via the shell and return its exit code.

    ``shell=True`` is required so user pipelines (``... | fabric -sp summarize``)
    work. Only the video url/id are injected and they are ``shlex.quote``d; the
    rest of the command is the user's own configuration.
    """
    result = subprocess.run(cmd, shell=True)
    return result.returncode
