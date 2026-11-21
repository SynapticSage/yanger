#!/usr/bin/env bash
# Enforce the house roadmap grammar, and detect the states merge=union can
# manufacture silently in ROADMAP.md.
#
#   ./check-roadmap-integrity.sh [path-to-ROADMAP.md]
#
# THE GRAMMAR OF RECORD is docs/roadmap-grammar.md (R102). This file refuses
# exactly what that document says it refuses, and nothing else — when the two
# disagree, the document wins and this file is wrong. Kept as one dependency-
# free bash script because `bootstrap.sh` copies it to every target's root and
# `loop merge` runs it there with nothing but `[[ -x ]]` to go on.
#
# `merge=union` (shipped with R020) resolves the four-writer contention by
# keeping both sides' lines with zero conflict markers — which is precisely the
# problem: "both lines kept" IS the failure mode when the lines are rival
# versions of one item. Two shapes matter, both mechanically decidable, both
# invisible to a human skimming a 400-line file:
#
#   1. Delete-vs-modify resurrection: one writer completes an item (bullet
#      removed, changelog line added) while another edits the bullet — union
#      keeps the edit, so the item sits in ## Roadmap AND ## Changelog at once.
#      That is the guardrails' named "illegal state", manufactured silently.
#   2. Same-bullet duplication: two writers edit one bullet's line → two
#      near-duplicate bullets with divergent statuses. The stop guard counts
#      the todo copy; the digest renders both.
#
# Both shapes apply to `(S<n>)` observations in ## Proposed as much as to
# `R<n>` items — the 2026-09-10 incident was an observation, and this file
# reported OK before and after it.
#
# By the repo's own three-tier rule this belongs in a check, not in prose.
# Run standalone, by run-tests.sh, by the loop's maintenance pass, and by
# `loop merge` against the target.
set -uo pipefail

# This file is copied to every target and run by that target's `loop merge`
# through `#!/usr/bin/env bash` — which on a stock macOS with no Homebrew is
# bash 3.2 (2007): no `mapfile`, and "${arr[@]}" on an empty array is an
# unbound-variable error under set -u until 4.4. Say so in one line rather
# than crash raw inside a merge gate; the gate reads a non-zero exit as
# "refuse", which is the safe direction (R051), and the line names the fix.
if (( BASH_VERSINFO[0] < 4 )); then
  echo "check-roadmap-integrity: needs bash >= 4 (found $BASH_VERSION) — install a newer bash (brew install bash) or run: /path/to/bash $0" >&2
  exit 1
fi

ROADMAP="${1:-$(dirname "$0")/ROADMAP.md}"
[[ -f "$ROADMAP" ]] || { echo "check-roadmap-integrity: no roadmap at $ROADMAP" >&2; exit 1; }

fail=0

section() {   # $1 = heading word after "## " — matched whole, so `## Proposed` never means `## Proposed-archive`
  awk -v h="## $1" 'index($0, h)==1 && (length($0)==length(h) || substr($0, length(h)+1, 1)==" ") {f=1; next} /^## /{f=0} f' "$ROADMAP"
}
active_body="$(section Roadmap)"
proposed_body="$(section Proposed)"
logged_body="$(section Changelog)"

# Arrays are expanded as ${arr[@]+"${arr[@]}"} throughout: on bash 4.0-4.3 a
# plain "${arr[@]}" of an EMPTY array is an unbound-variable error under set -u
# (fixed in 4.4), and a roadmap with no gates or no observations is the
# ordinary case, not the edge.
# Item IDs in the active section (## Roadmap up to the next ## heading).
# grep the ID straight out — `- [ ]` field-splits unpredictably (the checkbox
# contains a space), which made an awk-column version report `]` as an item ID.
# Anchored to the bullet's LEADING ID only — a bare R-number grep would pick up
# every cross-reference in the bullet's prose and report phantom duplicates.
mapfile -t active < <(sed -nE 's/^- \[.\] (R[0-9]+[a-z]?) .*/\1/p' <<<"$active_body")
# A converted roadmap keeps its own ids (R143): an active bullet led by a foreign
# id is an item too, so it is counted and checked for duplicates and resurrection
# like a house one. Gates have rules of their own below. The pattern is the
# reader's FOREIGN_ID, the same as id_foreign further down.
mapfile -t active_foreign < <(sed -nE 's/^- \[.\] ([A-Za-z]+[.-]?[0-9]+([.-][A-Za-z0-9]+)*[a-z]?|[0-9]+(\.[0-9]+)+[a-z]?) .*/\1/p' <<<"$active_body" \
  | grep -vE '^(R[0-9]+[a-z]?|[Gg][Aa][Tt][Ee]-.*)$')

# 2 — duplicates within the active list
dupes="$(printf '%s\n' ${active[@]+"${active[@]}"} ${active_foreign[@]+"${active_foreign[@]}"} | sort | uniq -d)"
if [[ -n "$dupes" ]]; then
  echo "duplicate item IDs in ## Roadmap (union kept rival versions of one bullet?):" >&2
  printf '  %s\n' $dupes >&2
  fail=1
fi

# 1 — an ID both active and in the changelog (resurrection)
mapfile -t logged < <(sed -nE 's/^- (R[0-9]+[a-z]?) .*/\1/p' <<<"$logged_body")
for id in ${active[@]+"${active[@]}"}; do
  for done_id in ${logged[@]+"${logged[@]}"}; do
    if [[ "$id" == "$done_id" ]]; then
      echo "item $id is in ## Roadmap AND ## Changelog — the delete-vs-modify" >&2
      echo "  resurrection union produces silently; one of them is wrong" >&2
      fail=1
    fi
  done
done

# 4 — the active bullet's shape (R102). Three rules, each one the reason a
# reader disagreed with another: a bold id is invisible to the duplicate check
# above; a missing (P<n>) leaves the selector nothing to rank; a missing
# status: is invisible to the stop guard, which counts `status: (todo|in-
# progress)` and nothing else — R089 and R090 were filed without one and two
# P1 incidents were uncountable while this file said OK. The LAST status:
# token on the line is the item's status (a park appends its verdict), and an
# annotation may follow it.
bullets=0
while IFS= read -r line; do
  [[ "$line" =~ ^-\ \[.\]\  ]] || continue
  bullets=$((bullets + 1))
  id="$(sed -E 's/^- \[.\] \**([^ *]+)\**.*/\1/' <<<"$line")"
  if [[ "$line" =~ ^-\ \[.\]\ \*\*R[0-9]+[a-z]?\*\* ]]; then
    echo "item $id is bold-wrapped — house ids are bare (- [ ] $id (P<n>) …)" >&2
    fail=1
  elif [[ "$line" =~ ^-\ \[.\]\ R[0-9]+[a-z]?\  ]] && ! [[ "$line" =~ ^-\ \[.\]\ R[0-9]+[a-z]?\ \(P[0-9]\) ]]; then
    echo "item $id has no (P<n>) priority tag directly after its id" >&2
    fail=1
  fi
  last_status="$(grep -oE 'status: (todo|in-progress|blocked)' <<<"$line" | tail -1)"
  if [[ -z "$last_status" ]]; then
    echo "item $id has no status: trailer (todo|in-progress|blocked) — invisible to the stop guard" >&2
    fail=1
  fi
done <<<"$active_body"

# 3 — gates-as-items (R008 Option A, ⚖️ h/h 2026-08-22): a dangling gate ref
#   (deps name a GATE- that is not an item) is refused below, with the deps
#   clause. It is the structural form of n1's live defect: conditionality that
#   LOOKS present but sits outside the graph the selector reads. Gate IDs are
#   `GATE-<id>` where <id> may contain hyphens (`GATE-2-prereg` is ONE id —
#   R102: the earlier class stopped at the first hyphen and read its own
#   dependent as dangling), matched at the bullet's leading position like
#   every other ID. Whether a gate gates anything — its scope — is rule 8's:
#   the orphan rule that lived here moved into the reader when R137 let a gate
#   declare its scope with `· blocks:`, so a filed stage gate passes this gate.
gate_class='[Gg][Aa][Tt][Ee]-[A-Za-z0-9-]+'
mapfile -t gate_lines < <(grep -E "^- \[.\] \**${gate_class}" <<<"$active_body")
gates=()
for gl in ${gate_lines[@]+"${gate_lines[@]}"}; do
  gates+=("$(sed -E "s/^- \[.\] \**(${gate_class})\**.*/\1/" <<<"$gl")")
done
# 6 — the deps clause (R132; the rules are docs/roadmap-grammar.md's, and
#   .claude/hooks/roadmap.py reads by the same ones — tests/roadmap-deps.test.sh
#   drives both on one set of fixtures). The clause is the FIELD `· deps:`
#   (emphasis allowed around either token), found after backticked spans are
#   removed, so a `· deps:` quoted in prose is not a clause: the unanchored
#   substring this replaced swallowed everything after a prose mention and
#   produced a dangling-gate refusal for a gate no bullet declared — three
#   times in five days, the third while documenting the second. Exactly one
#   clause per bullet; its value is `none` or a run of ids ending at the first
#   token that is not an id; every id in the run must be an item somewhere.
id_house='R[0-9]+[a-z]?'
# The reader's FOREIGN_ID, kept identical: either shape may end in one lowercase
# insertion letter, 1.15g or S.8b (R143).
id_foreign='[A-Za-z]+[.-]?[0-9]+([.-][A-Za-z0-9]+)*[a-z]?|[0-9]+(\.[0-9]+)+[a-z]?'
id_class="^(${id_house}|${gate_class}|${id_foreign})$"
# `· needs:` is the same field as `· deps:` (R167, Q2), so a bullet carrying both
# has two clauses. A heading's clause and a gate's alias (R167, Q1 and Q4) are
# the reader's rules, run in rule 8; here an alias is only a known id.
clauses_of() { sed -E 's/`[^`]*`//g' <<<"$1" | grep -oE '· \*{0,2}_?(deps|needs):\*{0,2}_?[^·]*' | sed -E 's/^· \*{0,2}_?(deps|needs):\*{0,2}_?[[:space:]]*//; s/[[:space:]]*$//'; }
# Logged ids: a house id or an S<n> list, bare; and a foreign id logged plain or
# bold, `- 1.14 …` or `- **1.14 — …**` (R143), read as the reader's LOGGED_FOREIGN
# reads it — the whole leading token, so `- 1.14-2 …` logs no 1.14 — and never an
# id with a form of its own (a house, S or Q id is logged bare; a gate is below).
mapfile -t logged_foreign < <(sed -nE "s/^- (\*\*)?(${id_foreign})((\*\*|[[:space:]]).*)?\$/\2/p" <<<"$logged_body" \
  | grep -vE '^(R[0-9]+[a-z]?|[SQ][0-9]+|[Gg][Aa][Tt][Ee]-.*)$')
# 1, for foreign ids: one both active and logged is the same resurrection.
for id in ${active_foreign[@]+"${active_foreign[@]}"}; do
  for done_id in ${logged_foreign[@]+"${logged_foreign[@]}"}; do
    if [[ "$id" == "$done_id" ]]; then
      echo "item $id is in ## Roadmap AND ## Changelog — the delete-vs-modify" >&2
      echo "  resurrection union produces silently; one of them is wrong" >&2
      fail=1
    fi
  done
done
# A gate's alias (R167): the first token of each `· alias:` value on a gate line,
# active or logged. It names its gate, so a dep on it is known; whether the alias
# itself is well formed and collides with nothing is the reader's rule 8.
mapfile -t aliases < <( { grep -E "^- \[.\] \**${gate_class}" <<<"$active_body"
                         grep -E "^- (\[[xX]\] )?\**${gate_class}" <<<"$logged_body"; } \
  | sed -E 's/`[^`]*`//g' | grep -oE '· \*{0,2}_?alias:\*{0,2}_?[^·]*' \
  | sed -E 's/^· \*{0,2}_?alias:\*{0,2}_?[[:space:]]*//' | awk 'NF {print $1}')
known_ids="$( { sed -nE 's/^- \[.\] \**([^ *]+)\**.*/\1/p' <<<"$active_body"
                sed -nE 's/^- ((R[0-9]+[a-z]?|S[0-9]+)(, ?S[0-9]+)*).*/\1/p' <<<"$logged_body" | tr ',' '\n' | tr -d ' '
                printf '%s\n' ${logged_foreign[@]+"${logged_foreign[@]}"} ${aliases[@]+"${aliases[@]}"}; } | sort -u)"
# A gate a human closed lives in ## Changelog as `- GATE-<id> …` — or as its
# checked bullet pasted whole, `- [x] GATE-<id> …`, which the reader reads as
# logged too (R137 G2) — and a ref to it is not dangling.
mapfile -t closed_gates < <(sed -nE "s/^- (\[[xX]\] )?\**(${gate_class})\**.*/\2/p" <<<"$logged_body" | tr '[:lower:]' '[:upper:]' | sort -u)
declare -A dep_edges=()      # active id -> its deps among ACTIVE ids (for the cycle check)
declare -A is_active=()
while IFS= read -r line; do
  [[ "$line" =~ ^-\ \[.\]\  ]] || continue
  id="$(sed -E 's/^- \[.\] \**([^ *]+)\**.*/\1/' <<<"$line")"
  is_active["$id"]=1
done <<<"$active_body"
# 1, for a foreign id: logged, it reads as done (R143), so one still active is the
#   same resurrection, and the reader would release what waits on it — even an
#   item a gate holds. Refused as a house id is; a person keeps one of the lines.
for x in ${logged_foreign[@]+"${logged_foreign[@]}"}; do
  if [[ -n "${is_active[$x]:-}" ]]; then
    echo "item $x is in ## Roadmap AND ## Changelog — the delete-vs-modify" >&2
    echo "  resurrection union produces silently; one of them is wrong" >&2
    fail=1
  fi
done
gate_refs=()
while IFS= read -r line; do
  [[ "$line" =~ ^-\ \[.\]\  ]] || continue
  id="$(sed -E 's/^- \[.\] \**([^ *]+)\**.*/\1/' <<<"$line")"
  mapfile -t cl < <(clauses_of "$line")
  if (( ${#cl[@]} == 0 )); then
    echo "item $id has no · deps: clause (or · needs:) — the grammar's active form carries one (· deps: none when it waits on nothing)" >&2
    fail=1; continue
  elif (( ${#cl[@]} > 1 )); then
    echo "item $id has ${#cl[@]} · deps: clauses — one field per bullet (quote a deps: mention in backticks to keep it out of the field)" >&2
    fail=1; continue
  fi
  value="${cl[0]}"
  read -ra toks <<<"$(tr ',;' '  ' <<<"$value")"
  run=()
  if [[ "${toks[0]:-}" =~ ^[Nn][Oo][Nn][Ee]$ ]]; then
    :
  else
    for t in ${toks[@]+"${toks[@]}"}; do
      [[ "$t" =~ $id_class ]] || break
      run+=("$t")
    done
    if (( ${#run[@]} == 0 )); then
      echo "item $id: deps clause starts with neither none nor an id (\"${value:0:40}\") — see docs/roadmap-grammar.md" >&2
      fail=1; continue
    fi
  fi
  edges=""
  for d in ${run[@]+"${run[@]}"}; do
    if [[ "$d" =~ ^[Gg][Aa][Tt][Ee]- ]]; then
      gate_refs+=("$(tr '[:lower:]' '[:upper:]' <<<"$d")")
    elif [[ "$d" =~ ^Q[0-9]+$ ]]; then
      :   # a question is never a dep: rule 8's reader refuses it, and says why (R151)
    elif ! grep -qxF -- "$d" <<<"$known_ids"; then
      echo "item $id depends on $d, which is in neither ## Roadmap nor ## Changelog — a dep that is no item is never satisfied" >&2
      fail=1
    fi
    [[ -n "${is_active[$d]:-}" ]] && edges="$edges $d"
  done
  dep_edges["$id"]="${edges# }"
done <<<"$active_body"
mapfile -t gate_refs < <(printf '%s\n' ${gate_refs[@]+"${gate_refs[@]}"} | sed '/^$/d' | sort -u)
for ref in ${gate_refs[@]+"${gate_refs[@]}"}; do
  found=0
  for g in ${gates[@]+"${gates[@]}"} ${closed_gates[@]+"${closed_gates[@]}"}; do
    [[ "$(tr '[:lower:]' '[:upper:]' <<<"$g")" == "$ref" ]] && { found=1; break; }
  done
  if (( ! found )); then
    echo "deps reference $ref but no such gate item exists — a dangling gate ref" >&2
    fail=1
  fi
done

# 7 — a dependency cycle among active items (R132). Kahn's peel: drop every
#   item whose active deps are all dropped; whatever remains is on or behind a
#   cycle, and following edges from the smallest remaining id prints one chain.
declare -A indeg=()
for id in "${!dep_edges[@]}"; do indeg["$id"]=0; done
for id in "${!dep_edges[@]}"; do
  for d in ${dep_edges[$id]}; do [[ -n "${indeg[$d]:-}" ]] && indeg["$id"]=$(( ${indeg[$id]} + 1 )); done
done
peeled=1
while (( peeled )); do
  peeled=0
  for id in "${!indeg[@]}"; do
    if (( ${indeg[$id]} == 0 )); then
      for other in "${!dep_edges[@]}"; do
        for d in ${dep_edges[$other]}; do [[ "$d" == "$id" ]] && indeg["$other"]=$(( ${indeg[$other]} - 1 )); done
      done
      unset 'indeg[$id]'; peeled=1
    fi
  done
done
if (( ${#indeg[@]} > 0 )); then
  start="$(printf '%s\n' "${!indeg[@]}" | sort | head -1)"
  chain="$start"; cur="$start"; seen=" $start "
  for _ in "${!indeg[@]}"; do
    nxt=""
    for d in ${dep_edges[$cur]}; do [[ -n "${indeg[$d]:-}" ]] && { nxt="$d"; break; }; done
    [[ -n "$nxt" ]] || break
    chain="$chain -> $nxt"
    [[ "$seen" == *" $nxt "* ]] && break
    seen="$seen$nxt "; cur="$nxt"
  done
  echo "dependency cycle: $chain — an item cannot wait on itself, however indirectly" >&2
  fail=1
fi

# 5 — observations in ## Proposed (R102, tightened 2026-09-10). The same two
# union shapes as 1 and 2, for `- **(S<n>)` bullets: duplicated in place, or
# present while the Changelog already retired the id (as `- S<n> …` or inside
# a comma list `- S1, S2, S9 …`). The older design-proposal form `- **S<n> — …`
# is a record that may outlive its promotion and is deliberately not read.
mapfile -t observations < <(sed -nE 's/^- \*\*\((S[0-9]+)\).*/\1/p' <<<"$proposed_body")
obs_dupes="$(printf '%s\n' ${observations[@]+"${observations[@]}"} | sort | uniq -d)"
if [[ -n "$obs_dupes" ]]; then
  echo "observation id(s) appear twice in ## Proposed (union kept rival versions of one bullet?):" >&2
  printf '  (%s)\n' $obs_dupes >&2
  fail=1
fi
mapfile -t retired < <(sed -nE 's/^- ((S[0-9]+)(, ?S[0-9]+)*).*/\1/p' <<<"$logged_body" \
  | tr ',' '\n' | tr -d ' ' | sed '/^$/d')
for o in ${observations[@]+"${observations[@]}"}; do
  for r in ${retired[@]+"${retired[@]}"}; do
    if [[ "$o" == "$r" ]]; then
      echo "observation ($o) is in ## Proposed AND retired in ## Changelog — resurrected" >&2
      echo "  (a ruled observation leaves ## Proposed and never returns)" >&2
      fail=1
    fi
  done
done

# 8 — a gate's scope (R137), read by the bundle's roadmap reader, `roadmap.py
#   check`: a gate with no blocks:, no dependent and no `· terminal` (the
#   orphan rule, extended), a blocks: id that is no active item, a gate that
#   blocks itself or its own evidence, blocks: beside `· terminal`, all mixed
#   with ids or an empty list, blocks: on a bullet that is not a gate, and a
#   gate id filed twice. The same call reads ## Open questions (R151): a bullet
#   there that is not a question, a question filed twice or reopened, one that
#   carries needs-human or a work field, and an item that depends on one. It
#   reads R167's two forms this body does not: a ###/#### heading's dependency
#   clause (its shape, its ids, and a cycle through it) and a gate's alias (only
#   a gate carries one, one foreign id, colliding with nothing); and it prints
#   R049's park-briefing warnings, which never change its exit code. It reads
#   R076's DEAD notes (an indented `- DEAD` line under a queue entry must carry
#   a backticked command and its exit code, and no ⚖️, needs-human or
#   status: marker) and its supersedes:/reinforces: relations (ids the file
#   holds), and warns on a premise that carries a field label. And
#   first it refuses what `loop status` calls UNREADABLE (R086), by the one
#   function both call: no ## Roadmap, or a ## Roadmap whose lines are written
#   as items when not one is an item here — so a file this gate passes is one
#   the status screen can read. These are the first rules ported out of this file
#   (docs/plans/deepen-2-roadmap-model-plan.md, decision 2): the block below
#   is written for bash 3.2 — no mapfile, no arrays — because the wrapper this
#   file is becoming must run on a stock macOS. The reader is looked for where
#   check-id-gloss.sh looks for its parser: this checkout's variant tree, then
#   an installed target's .claude/hooks/. One that is missing or cannot run
#   refuses, never passes: a merge gate that could not look has not looked (R051).
_here="$(cd -P "$(dirname "$0")" && pwd)"
_reader=""
for _p in "$_here/bundle/hooks/roadmap.py" "$_here/.claude/hooks/roadmap.py"; do
  if [[ -f "$_p" ]]; then _reader="$_p"; break; fi
done
if [[ -z "$_reader" ]]; then
  echo "check-roadmap-integrity: no roadmap.py at $_here/bundle/hooks/ or $_here/.claude/hooks/ — re-run \`loop update\`" >&2
  fail=1
else
  python3 "$_reader" check "$ROADMAP"; _rc=$?
  if (( _rc == 1 )); then
    fail=1
  elif (( _rc != 0 )); then
    echo "check-roadmap-integrity: $_reader could not run its check (exit $_rc) — re-run \`loop update\`" >&2
    fail=1
  fi
fi

# A guard that can pass by finding nothing must say when it found nothing. Any
# id-shaped word is a foreign id, so one logged counts as found only in a file
# with a ## Roadmap heading (R143): a finished converted roadmap passes, while a
# file not in this grammar — a roadmap sectioned by phase, not yet converted —
# is refused, whatever its Changelog leads with.
heading=0; grep -qE '^## Roadmap( |$)' "$ROADMAP" && heading=1
if (( ${#active[@]} == 0 && ${#logged[@]} == 0 && bullets == 0 && (${#logged_foreign[@]} == 0 || ! heading) )); then
  echo "check-roadmap-integrity: found no item IDs at all in $ROADMAP — wrong file?" >&2
  exit 1
fi

(( fail )) && exit 1
mapfile -t questions < <(sed -nE 's/^- \*\*\((Q[0-9]+)\).*/\1/p' <<<"$(section "Open questions")")
echo "roadmap integrity OK — $(( ${#active[@]} + ${#active_foreign[@]} )) active, $(( ${#logged[@]} + ${#logged_foreign[@]} )) logged, ${#gates[@]} gates wired, ${#observations[@]} observations, ${#retired[@]} retired, ${#questions[@]} open questions; no duplicates, no resurrections, every bullet shaped, every deps clause read, no cycles, every gate scoped"
