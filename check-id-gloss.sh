#!/usr/bin/env bash
# Fail if a loop-written document mentions a roadmap id it never explains (R032).
#
#   ./check-id-gloss.sh journal/2026-08-23_1709_R027-graceful-stop-sentinel.md
#   ./check-id-gloss.sh journal/*.md
#
# THE RULE (ratified 2026-08-23): whenever an `R0xx`, `S-n`, `(Pn)` or `GATE-x`
# appears in something the loop wrote — a journal entry, a run report, a reviewer
# report — it comes with a one-line summary of what that item is. The reader's
# working memory is assumed not to hold the program. Someone opening a journal
# entry a week later should not have to go find the roadmap to parse the first
# sentence.
#
# The mechanical form is an `## Items` table at the top of the document. The
# parser lives in id_gloss.py, not in a heredoc here, for the reason
# loop-run.sh:77 states: a NameError inside a shell heredoc crashed a live run
# because no linter can see code that has no file. It lives in the BUNDLE
# (bundle/hooks/, R102) so that `bootstrap.sh` installs it
# into a target's .claude/hooks/ with every other hook file, and this wrapper —
# copied to the target's root by the same install — finds it there.
#
# DELIBERATELY NOT WIRED TO `journal/*.md` IN run-tests.sh. The blueprint
# suggested that; it would turn the suite red on 16 pre-existing entries written
# before this rule existed, which would block every later item's CHECK OFF on an
# unbudgeted retrofit. The rule is enforced where documents are WRITTEN (see
# dev-loop.md § 4 and § Stop & report), and this script is what enforces it —
# run it on the files you just wrote.
set -uo pipefail
# Deliberately does NOT `cd` into the repo, unlike its sibling checks. They scan
# fixed repo paths; this one takes paths from the CALLER, and cd-ing first turns
# `check-id-gloss.sh journal/entry.md` from a sibling directory into a
# file-not-found. Resolve the script's own location instead and leave the
# caller's cwd alone.
_here="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Two homes: this source checkout keeps the parser in the variant tree; an
# installed target has it under .claude/hooks/ (bootstrap.sh copies the whole
# hooks subtree). Anything else is an incomplete install, said plainly.
# Run it THROUGH python3 rather than exec'ing the file: a copy that lost its
# executable bit would otherwise make `exec` fail, and a non-interactive bash
# exits on a failed exec without ever reaching the second candidate or the
# message below (code review, 2026-09-23).
for _p in "$_here/bundle/hooks/id_gloss.py" "$_here/.claude/hooks/id_gloss.py"; do
  [[ -f "$_p" ]] && exec python3 "$_p" "$@"
done
echo "check-id-gloss: no id_gloss.py at $_here/bundle/hooks/ or $_here/.claude/hooks/ — re-run \`loop update\`" >&2
exit 1
