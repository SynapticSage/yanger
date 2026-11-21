# YouTube Ranger (yanger) — Roadmap

## Goal

Status: **alpha**. This roadmap was synthesized from a three-lens discovery pass
(gap-analysis + emerging opportunities, architecture/tech-debt, product/features)
against the merged codebase. Items are proposals, prioritized by leverage.

**Legend** — Impact = user-visible value (High / Med / Low). Effort = S (≤ ~½ day) ·
M (~1–3 days) · L (> 3 days). "Velocity unlock" = makes later work cheaper/safer.

**Ids (added by the conversion to the loop's grammar).** Source ids are kept: `0.n` = Tier 0
table row, `1.n` = Tier 1 item #n, `2.n` / `3.n` = the n-th Tier 2 / Tier 3 bullet, `D.n` = the
n-th "Discovered during /loop" follow-up, `H.n` = a headline-track slice or deferred piece.

### Guiding theme

yanger's biggest risk is not missing features — it's **duplication on untested
paths**. Both prior Criticals lived on zero-coverage code, and two tests were
*masking* bugs by mocking nonexistent method names. The transcript fetch+cache
logic is hand-copied in four places and has already diverged into a live bug (see
below). A few foundational consolidations make every subsequent feature — including
the headline custom-command registry — cheaper and safer to build.

### Suggested sequencing (source; order, not dependencies)

- **Now:** §0 live-bug fix + Tier 0 quick wins.
- **Next:** Tier 1 #1 (transcript service) + #2 (test harness) — these unblock everything else.
- **Then:** the headline custom-command registry (core `:run` first; the gated MCP tool once
  elicitation lands).
- **Ongoing:** Tier 2 MCP deepening; Tier 3 once foundations are in.

### Non-goals — dropped, won't build (explicit product decisions)

**`:play` external player (mpv/vlc) — DROPPED (2026-07-02, user decision).** Piping YouTube
video streams into an external player (mpv/vlc, typically via `yt-dlp`) to *watch* videos
sits crosswise to YouTube's Terms of Service (playback outside the YouTube player / embedded
player). yanger stays an *organization/management* tool. Users who want this can still wire it
themselves through the general custom-command registry (their own config, their own posture) —
we simply don't ship or endorse a first-class `:play` verb. `r` (open in browser) remains the
supported "watch" affordance. *Note: this does not restrict the custom-command registry, which
is content-agnostic shell the user configures.*

### Reference — Appendix A: Built vs intended (vs the CLAUDE.md vision)

| Feature (intended) | Status | Note |
|---|---|---|
| Three-column miller view; hjkl/gg/G; cut/copy/paste; dD; cw; sort/filter/search | DONE | Core TUI solid |
| Visual mode / invert / unmark | DONE | `V`=visual, `v`=invert (spec says reverse — drift) |
| Undo/redo | PARTIAL | Full in TUI; **MCP mutations bypass it** |
| Command mode (`:`) | DONE | ~15 commands |
| Offline SQLite cache | DONE | ~95% API reduction |
| Duplicate detection; statistics | DONE | TUI + MCP |
| `H`/`L` history; `gs` settings; `:move`/`:backup` | MISSING | Spec'd, never built |
| Macros (`qa`/`@a`) | MISSING | Not started |
| Colorschemes (default/nord) | MISSING | Dead config string; no loader (Tier 0.11) |
| Custom keybindings (YAML) | MISSING | Registry hardcoded (Tier 3) |
| Plugin system; smart playlists; sharing; merge/split | MISSING | See Decision 2 |
| mpv/vlc playback | DROPPED | ToS posture — see "Dropped"; `r` opens browser; users can DIY via custom-command registry |
| Video download | PARTIAL | Via `:transcript`/custom-command hook, not built-in |
| **Beyond spec (built):** MCP server (~20 tools), Takeout import + Puppeteer `sync`, transcript fetch/cache/proxy + Webshare, `:transcript`/`:set` hook, bulk edit, multi-format export | DONE | README/journals ahead of the vision doc |

### Reference — Appendix B: External opportunities (as of mid-2026) & references

Installed: `mcp 1.25.0`, `textual 6.5.0`, `youtube-transcript-api 1.2.3`,
`google-api-python-client 2.187.0`.

> **Textual** native theme system (`App.theme` + `register_theme`, since 0.86) and the
> `ctrl+p` command palette; latest is 8.x — closes the colorscheme gap cheaply.
>
> **MCP spec** (current stable 2025-11-25) adds **elicitation**, **sampling**, structured
> tool output, and resources/prompts — yanger's server is tools-only today. Dynamic tool
> registration via `notifications/tools/list_changed` exists but **client support is uneven**
> (design a generic `run_command` fallback).
>
> **Claude Skills** (custom slash-commands merged into Skills) are the distribution path for
> a command registry; latest Claude line (Opus 4.8) + Tool Search Tool / programmatic tool
> calling keep large tool libraries usable.
>
> **Transcripts:** cloud-IP blocking remains the dominant failure mode; Webshare requires
> *rotating Residential* proxies (document this); PoToken support is in progress; a hosted
> fallback can ride the `transcript_command` hook.
>
> **YouTube Data API v3:** stable (10k/day quota); no uploads so the `videos.insert` cost
> hike is irrelevant; favorites-playlist modification is deprecated (guard it).

References:

> Textual: changelog · command-palette guide (textual.textualize.io)
> MCP: modelcontextprotocol.info/specification · tools spec · transports-future blog · client-capability-gap (pulsemcp)
> Claude: code.claude.com/docs/en/skills · anthropic.com/engineering/advanced-tool-use · platform.claude.com programmatic-tool-calling
> Transcripts: pypi.org/project/youtube-transcript-api · jdepoix/youtube-transcript-api#593 (cloud IP blocking)
> YouTube API: developers.google.com/youtube/v3/revision_history

*Sources for this roadmap: the project journals (`./journal/`, local/gitignored), the
CLAUDE.md vision spec, `README.md`, `docs/MCP_SERVER_PLAN.md`, and a three-agent discovery
pass over the merged codebase.*

## Roadmap

### Discovered during /loop (follow-ups)

- [ ] D.3 Bulk-edit renames are silently dropped but reported as done · context: (Surfaced by the 0.8 arch review.) The live apply path `operation_history.BulkEditOperation.execute` never iterates `changes.renames`, yet the preview renders a "Renames" section, `summary()` counts them, and the success toast says "Bulk edit completed" — so a user who renames an item in the bulk editor is told it worked while nothing happened. (YouTube API can't rename playlist *items*; playlist/video title renames go through `cw`.) *Fix: either drop renames from the bulk-edit preview/summary, or surface them as an explicit "not applied (unsupported)" result. Small, UX-correctness.* **⏭️ Skipped in the 2026-07-07 /goal pass:** the fix embeds a real product decision — DROP bulk rename-detection vs. actually WIRE renames through `rename_playlist`/`update_video_title` (make the feature real). That's a human call, not an obvious mechanical fix; and it touches `app.py`, which the fable-cmdinput agent has open. · touches: `operation_history.py`, `app.py` · status: todo · deps: GATE-bulk-rename
- [ ] GATE-bulk-rename adjudicate: Bulk-edit renames (D.3) — drop them from the bulk-edit preview/summary, surface them as an explicit "not applied (unsupported)" result, or wire them through `rename_playlist`/`update_video_title` so the feature is real? · on-drop: D.3 removes rename detection from the bulk-edit preview, `summary()` and toast · on-surface: D.3 keeps detection but reports renames as "not applied (unsupported)" · on-wire: D.3 applies renames via `rename_playlist`/`update_video_title` (a feature build, not a small fix) · blocks: D.3 · status: todo · deps: none

### ★ Headline track — User-defined custom-command registry (+ agent-domain integration)

> **Confirmed top priority (user request, 2026-07-02):** "a config file where users can set
> custom command names to bash commands or pipes of commands to run on a video URL." That is
> exactly this track — a YAML `commands:` registry mapping names → shell templates/pipes with
> `{url}`/`{id}` placeholders, run against the current video or a selection. No separate item is
> needed; this headline *is* the feature. Build the **core registry + `:run`** slice first.
>
> Generalize the existing single `:transcript` hook into a **named registry of shell
> commands** users can run on the current video *or* a selection. Pure reuse of
> `src/yanger/core/transcript_command.py` (`build_command`/`resolve`/`run`, `shell=True`,
> `shlex.quote`'d `{url}`/`{id}`) and existing selection helpers
> (`miller_view.video_column.get_marked_videos()`, visual mode) + config plumbing
> (`save_user_setting`, env merge in `load_settings`).

#### Config schema

```yaml
# ~/.config/yanger/config.yaml
commands:
  dl:    "yt-dlp {url}"
  sum:   "yeet {url} | fabric -sp summarize"
  archive: "archivebox add {url}"
  # long form opts into batch + confirmation:
  dlall: { run: "yt-dlp {urls}", mode: batch, confirm: true }
```

> Bare `name: "template"` ⇒ `{run: template, mode: per-video, confirm: false}`.
> Env override convention `YANGER_CMD_<NAME>` (mirrors `YANGER_TRANSCRIPT_COMMAND`);
> add a `commands` branch to `_USER_SETTING_SECTIONS` so `:set` can persist them.
> `transcript_command` becomes the reserved default-named command → **back-compat,
> no migration**.

#### Placeholders & set-semantics

> Single video: `{url}`, `{id}`, plus `{title}` (filenames).
> Selection — two modes, chosen per-command via `mode:` —
> **`per-video`** (default): run once per selected video (each `shlex.quote`'d);
> **`batch`**: one invocation with `{urls}`/`{ids}` (space-joined, quoted) or stdin
> (newline-joined) for `xargs`/`fabric`-style tools.
> Default on a selection with no `mode:` is **per-video** (least surprising).

#### TUI invocation UX

> `:run <name>` (new branch in `execute_command`) — operates on marked/visual
> selection if any, else current video (mirrors the `dd`/`yy` marked-else-current
> convention).
> Tab-completion of command names; optional user-bindable keys (`keybindings:` →
> `:run <name>`); optional later: a quick fuzzy command palette.

#### Execution & safety

> `shell=True` (user's own config; same posture as `transcript_command`/`$EDITOR`),
> only injected URLs/IDs are `shlex.quote`'d.
> `with self.suspend(): asyncio.to_thread(run)` for interactive/streaming tools.
> Output: per-video interactive streams live; batch/capture → scrollable modal or
> `$PAGER`; non-zero exits surfaced per video.
> **Confirm** modal when selection > threshold (e.g. 5) or `confirm: true`.
> It runs arbitrary user-configured shell — acceptable/user-owned; guardrails:
> confirm-on-large-selection, quote-only-injection, never auto-run on startup,
> document clearly.

#### Slice priorities (source)

> **Slice priorities:** core registry + `:run` = **High / M / no deps** → generic MCP
> tool = High / M (dep: registry + opt-in gate) → analysis-cache wiring = High / S-M →
> skill + chaining + prompts = Med / strategic.
>
> **Note:** `Settings.commands` must be wired at all **four** touch-points (field, `from_dict`,
> `merge`'s section list, `save_settings`) or `commands:` is silently dropped.

#### Headline items

- [ ] H.4 Commands as LLM pipelines — analysis-cache wiring · context: capture command stdout as an **analysis artifact** keyed to the video, reusing the transcript/`fabric_analyze` cache → returnable via a new `get_analysis`; unify TUI-run and MCP-run results in one store. *Slice priority: High / S-M (third slice after core registry → generic MCP tool).* · status: todo · deps: H.3
- [ ] H.5 Claude Skill / slash-commands + LLM chaining + prompts · context: **Claude Skill / slash-commands** that surface the registry to the agent and can **author new commands** into config (with confirmation); Agent-Skills packaging. **LLM chaining**: agent composes registry commands across a selection (transcript → summarize → file note), mapping onto the per-video/batch semantics. *Slice priority: skill + chaining + prompts = Med / strategic (fourth slice, after analysis-cache wiring).* · status: todo · deps: H.4
- [ ] H.6 Inject `transcript_command` into the registry as the reserved default-named command · context: Still deferred after slice 2: injecting `transcript_command` into the registry (dual-source-of-truth — back-compat lives in the untouched `:transcript` branch). Design intent: `transcript_command` becomes the reserved default-named command → back-compat, no migration. · detail: built and merged (08d1ef0, merge 13a10ee). `:run transcript` resolves through the same resolver as `:transcript`; TUI-only, so MCP run_custom_command does not see it. An explicit `commands.transcript` or YANGER_CMD_TRANSCRIPT keeps the name (with its confirm gate) and a warning is logged · options: A — keep as built: an explicit entry wins, so `:run transcript` and `:transcript` can run different templates; B — flip it: transcript_command always owns the name and `:set commands.transcript` is refused (replace the setdefault in load_command_registry with an assignment) · ask: when both exist, which wins `:run transcript`? · touches: src/yanger/core/custom_command.py, src/yanger/app.py · evidence: fanout/20261119-1900/cmd-transcript-set worker notes · status: blocked (2026-11-21 — decision/merge-walk: the precedence between an explicit commands.transcript and transcript_command needs a person's ruling) · deps: none

### Tier 0 — Quick wins (S effort, clear value, ship first)

- [ ] 0.12 Status-bar contextual hint line — Discoverability (the CLAUDE.md mock shows it) · evidence: `ui/status_bar.py` · detail: ⏸️ **0.12 DEFERRED:** a hint line already exists (`status_bar.update_hints` shows `yy:copy dd:cut pp:paste`, matching the CLAUDE.md mock). Making it *contextual* requires resolving the shared center-widget status/hint contention — a UX refactor out of proportion to a "quick win" and regression-prone. Left for a dedicated UI pass. · status: todo · deps: none

### Tier 1 — Foundations (do before/with the headline; unlock velocity + safety)

- [ ] 1.3 Narrow `except Exception` · context: 🔶 **STARTED.** Done: both **bare `except:`** in `takeout.py` → `(ValueError, TypeError)`; and all **14 handlers in `operation_history.py`** → a shared `_OPERATION_API_ERRORS = (HttpError, QuotaExceededError)` tuple, so a real API/quota error is a clean `return False` while a bug (AttributeError/KeyError from a stale id) propagates instead of being masked. +2 harness tests prove both behaviors (built on the #2 fake). **Remaining (~66 sites in app/mcp/cli/etc.):** need per-site failure-mode analysis — file I/O → `OSError`, subprocess → `CalledProcessError`, JSON → `JSONDecodeError`, SQLite → `sqlite3.Error` — NOT a uniform sweep. *Impact Med-High, Effort M, continue on the #2 safety net.* (The 2026-07-07 /goal pass skipped the tail: needs per-site failure-mode judgment; not mechanically obvious.) · status: in-progress · deps: 1.2

### Tier 2 — MCP / LLM surface deepening


### Tier 3 — Strategic / larger

- [ ] 3.1 Decompose `app.py` (2,690 lines) · context: Extract the 185-line command dispatcher (`execute_command`) + ranger handlers → a `commands/` module; move transcript/metadata domain ops → `core/` (realizes Tier-1 #1). Shrinks the god-object, eases testing, realigns to the CLAUDE.md structure. *Med, L, after #1.2, velocity unlock.* · status: todo · deps: 1.2
- [ ] 3.2 Takeout improvements · context: Remaining: validate the live Takeout DOM against a real account, including the `yanger sync --resume` browser step (opening takeout.google.com/manage, an assumed URL, and clicking Download), which nobody has run against a real account. Built and merged (353bf39, merge 2a58e3d): JSON history import preserving watched-at, title and channel; zip import reads videos.csv like directory import, across split parts; `yanger sync --resume` download-only mode. Earlier source text: JSON history import (preserve watched-at timestamps; current HTML parse only recovers IDs; fix the `takeout.py:314` `Z`-timestamp no-op — **fixed 6cc03fa**, see Changelog); zip-import metadata enrichment; `sync` resume/download-only mode (watch for the emailed zip instead of re-submitting an export); validate the live Takeout DOM against a real account. *Med, M.* (The /goal pass skipped it: multi-part, wants real Takeout data.) · status: todo · deps: none
- [ ] 3.3 Centralize path resolution · context: Four roots in use (`~/.config/yanger`, `~/.cache/yanger`, `~/.yanger`, a `.cache/yanger` config default). One XDG-style module (config/cache/state) consumed by everything incl. `reset`. *Low-Med, S.* **Note (0.3 review):** `settings.cache.directory` (default `.cache/yanger`, overridable via `YANGER_CACHE_DIR`/`config.yaml`) is **dead config** — assigned but never passed into any `PersistentCache(...)`, which is why 0.3's `default_cache_dir()` is correct today. Centralization must reconcile this: either wire the setting through (and have `reset` consume the same resolver) or delete it. Until then it's a latent divergence trap. (That reconciliation shipped in c92f6c3 — see Changelog; the four-root centralization remains.) · status: todo · deps: none

## Proposed

- **(S1) Drop the `origin/claude/simple-rich-ui-8ZHYs` branch** (source: Decisions needed #1). A parallel, untested second UI (a 584-line `rich` menu) re-implements auth + loading independently of `app.py` — the exact untested-duplicate pattern behind prior Criticals; no documented need. The CLI + MCP already are the non-TUI surface. If accessibility/fallback ever becomes a real reported need, add a `--no-tui` mode over the existing CLI verbs (with tests), not a third UI. · needs-human
- **(S2) Explicitly drop/defer low-fit vision features** (source: Decisions needed #2) so they stop reading as perpetual TODO: macros (`qa`/`@a`), plugin system, smart/auto playlists, playlist sharing, merge/split as a user op. They're large scope and off the app's actual momentum (TUI + MCP/LLM). · needs-human
- **(S3) Reconcile the drifted `CLAUDE.md` vision with reality (or rewrite it)** (source: Decisions needed #3): it describes a `commands/`/`core/` package layout, `core/` classes, and `api_key` auth that don't exist, and `v`/`V`/`gr`/`gR`/`gs`/`H`/`L` keys that differ from the shipped keymap. · needs-human
- **(S4) User-bindable keys for registry commands** (source: headline TUI invocation UX, marked optional): `keybindings:` → `:run <name>`. · needs-human
- **(S5) Quick fuzzy command palette for registry commands** (source: headline TUI invocation UX, "optional later"). · needs-human
- **(S6) Document that Webshare requires rotating Residential proxies** (source: Appendix B, "(document this)"): cloud-IP blocking remains the dominant transcript failure mode. · needs-human
- **(S7) Bundle default Fabric pattern prompts (e.g. summarize, extract_wisdom)** (source: 2.4 worker): MCP sampling reads only `~/.config/fabric/patterns/<name>/system.md`, so with no Fabric files on disk a pattern fails closed with pattern_not_found. · needs-human
- **(S8) Confirm the order `backend: auto` tries for fabric_analyze/_batch** (source: 2.4 worker): shipped sampling first (the client's model), then local Fabric. The planner's default was local Fabric first; as shipped, a user with Fabric installed now sends analysis to the MCP client's model. A one-line flip in `YangerMCPServer._resolve_llm_backend`. · needs-human
- **(S9) Raise the `[mcp]` extra to `mcp>=1.25` and upgrade the live MCP runtime** (source: 2.1/2.2 workers): the live `yanger mcp` runs homebrew Python 3.10 with mcp 0.9.1, which the extra (`mcp>=1.0.0`) still allows. There, 2.1's confirm prompts are inert and 2.2's undo/redo always refuse. · needs-human
- **(S10) A default-off `mcp.allow_undo` setting** (source: 2.2 worker): lets MCP clients without elicitation use undo/redo, which otherwise always refuse on them. · needs-human
- **(S11) Persist and/or share the undo stack between the TUI and MCP** (source: 2.2 worker): today it is in memory, per process. Needs serialisable operations and a policy for cross-process conflicts. · needs-human
- **(S12) MCP cleanup after the registry changes** (source: H.7/H.8 and 2.1 workers): leave `{title}`, stdin and TUI-only commands out of the run_custom_command enum (today they are listed, then refused with a generic ValueError; return a structured `tui_only` error); drop the stale `{title}` NOTE in mcp_server.py; update the stale MCPSettings docstring in settings.py (confirm commands now run when the client can elicit); pass `stdin=subprocess.DEVNULL` in run_command_capture, which under MCP stdio inherits the protocol pipe. · needs-human
- **(S13) MCP virtual-playlist ids disagree between tools** (source: 2.3 worker, pre-existing bug): list_playlists(include_virtual) returns the bare uuid, while list_videos and get_playlist pass a `virtual_` id unstripped to get_virtual_videos, so a real Takeout virtual playlist returns []. A MagicMock in tests/test_mcp_server.py hides it. · needs-human
- **(S14) Undo robustness** (source: 2.2 worker, mostly pre-existing): PasteOperation and DeleteVideosOperation undo is not atomic and a retry after a partial failure duplicates or fails; an entry that always fails blocks every older one (needs a confirmed skip/discard verb); redo of a create mints a new playlist id, stranding later redo entries; redo of add_video loses `position`. · needs-human
- **(S15) Dead `keybindings:` config** (source: 3.5 worker): settings.py `KeybindingSettings` and default_config.yaml's `keybindings:` section are read by nothing now that overrides live in keybindings.yaml. Delete, rewire to the new loader, or warn when set. · needs-human
- **(S16) Takeout cache follow-ups** (source: 3.2 worker): a merge-mode re-import does not backfill added_at on cached History rows (`--replace` is the workaround); the TUI auto-fetch on folder load (app.py) still selects only untitled rows, so JSON-imported rows are enriched only by `M` and `yanger fetch-metadata`; videos the API cannot return keep metadata_fetched_at NULL and are re-queried on every fetch-metadata run. · needs-human
- **(S17) Escape user text before it reaches the markup-parsing status bar** (source: H.9 worker, pre-existing): e.g. handle_run_command's `Unknown command '{name}'` and the ConfirmationModal's `Command: {spec.template}` read `[...]` as styling. · needs-human

## Open questions

- **(Q1) Should MCP run_custom_command also see the reserved `transcript` command when `mcp.allow_custom_commands` is on?** Built TUI-only, fail-closed (H.6).
- **(Q2) Should the `yanger://commands` MCP resource show each command's template text?** It shows name/mode/confirm only, because templates (YANGER_CMD_* from the client's environment) may hold secrets (2.3).
- **(Q3) Should a keybindings.yaml entry add keys alongside an action's defaults, or `[]` unbind it, instead of replacing them?** Built: replace, and an empty list is rejected (3.5).
- **(Q4) Should keybinding overrides live in a `keybindings:` section of config.yaml instead of a separate keybindings.yaml?** Built: a separate `~/.config/yanger/keybindings.yaml`; S4 (bind keys to `:run <name>`) assumed config.yaml (3.5).
- **(Q5) Should the MCP confirm prompt also gate move_video and copy_videos?** Built: only delete_playlist, remove_video and `confirm: true` commands, matching the TUI's confirms (2.1).
- **(Q6) Should JSON watch-history ad records ("From Google Ads") be left out of History (Imported)?** Kept, as the HTML parse does (3.2).

## Changelog

Completed items land here (newest first) with the commit that shipped them. Full
per-run detail lives in the gitignored `journal/`.

- **3.5 — User-editable keybindings (keybindings.yaml).** ✅ DONE (fan-out run 20261119-1900, merged 13a10ee). `~/.config/yanger/keybindings.yaml` maps action ids to keys; each entry replaces that action's defaults. Bad entries and conflicts fail closed: a warning, and the defaults stay. Reserved: tab, shift+tab, ctrl+c, ctrl+p, ctrl+q. BINDINGS keys are rebound through Textual's set_keymap; a registry KeySequenceMatcher replaces the prefix branches in on_key. `config/keybindings.yaml` is a commented template. Planned, plan-critiqued, code-reviewed and critic-reviewed; every finding answered.
- **H.10 — Tab-completion of `:run` command names.** ✅ DONE (4b76d56, review fix 7149ff2; merged 13a10ee). `:run d` + Tab → `:run dl`, from the same registry `:run` resolves; names added with `:set commands.<name>` appear at once. Bare `:run ` offers no completion; `:run` with no name lists the names. A registry that fails to build gives no completion instead of crashing. Also fixed: reopening command mode left the cursor before the pre-filled `:`.
- **H.9 — `:set commands.<name>` persistence.** ✅ DONE (08d1ef0, a3579d7; merged 13a10ee). Bare-string templates only; a long-form entry, an empty name or template, and a non-mapping `commands:` are refused. save_user_setting now raises on an unparseable config.yaml instead of rewriting it with only the new key (also fixes `:set transcript_command`).
- **H.8 — `{title}` placeholder.** ✅ DONE (572c5f0; merged 13a10ee). Per-video and TUI-only. Always sanitized (letters, marks, numbers and `_ . , -` kept, everything else including spaces becomes `_`, no word starts with `-`), then shlex-quoted; proven inert in quoted, sh -c, eval and xargs contexts by a real-shell test matrix. A missing or empty title refuses the run before anything executes.
- **H.7 — Batch-mode stdin.** ✅ DONE (572c5f0; merged 13a10ee). `stdin: true|urls|ids` on `mode: batch` pipes the newline-joined URLs or ids into one invocation and suppresses the URL auto-append. TUI-only; an invalid value or stdin on a per-video command is refused at `:run` time with the reason.
- **2.2 — MCP undo parity.** ✅ DONE (e1e83a7; merged 2594310). In memory, per MCP server process (TUI parity). create/rename playlist and add/remove/move/copy videos are recorded when cache-verified undo data exists (else `undoable: false` plus an undo_note); new undo/redo tools always need the elicitation confirm, so they refuse on mcp 0.9.1 (see S9, S10). Also fixed DeleteVideosOperation redo (stale playlist_item_id), which repairs TUI redo of dD.
- **2.4 — MCP sampling backend for fabric_analyze/_batch.** ✅ DONE (20c1e34; merged 2594310). `backend: auto|fabric|sampling`; auto = the client's model via sampling, else local Fabric, else a fabric_not_installed error naming both remedies; no cross-fallback once a backend is chosen. Precedence awaits S8.
- **2.3 — MCP resources and prompts.** ✅ DONE (c3dafd6; merged 2594310). Cache-only resources `yanger://playlists`, `yanger://playlists/{id}` (virtual playlists as `virtual_<uuid>`), `yanger://transcripts` (index, cap 1000), `yanger://transcripts/{video_id}` (capped), and `yanger://commands` (only with `mcp.allow_custom_commands`, names/mode/confirm, never templates). Fabric patterns exposed as MCP prompts; an optional video_id appends the full cached transcript. No read authenticates, spends quota or fetches a transcript.
- **2.1 — MCP elicitation confirm gate.** ✅ DONE (4da5f97; merged 2594310). Asks the client's user to confirm delete_playlist, remove_video and `confirm: true` custom commands (the prompt lists the exact resolved command lines); only an explicit accept with confirm ticked proceeds, and decline, cancel, error or a 300 s timeout refuse. Clients without elicitation, and mcp 0.9.1, behave as before (see S9).
- **Takeout JSON history, zip videos.csv and `sync --resume` built (353bf39, merged 2a58e3d)** — the bulk of 3.2; live Takeout DOM validation and the unverified `--resume` browser step remain open under 3.2.
- **H.3 — gated MCP `run_custom_command` tool (headline slice 3).** ✅ DONE (resumed loop 2026-07-08). Shipped *before* elicitation (deviation from the original sequencing), substituting: opt-in gate (`mcp.allow_custom_commands`, default OFF), `confirm:true` commands excluded-from-enum AND refused, and a fan-out cap that rejects (not truncates). Adversarial-critic + code + arch review; the code review caught a **CRITICAL command-injection** (a template embedding `{id}` inside quotes defeats `shlex.quote`, so a hostile LLM-supplied id `$(...)` executed) — fixed by validating every id to the 11-char YouTube charset via a shared `models.is_valid_video_id` *before* building any command. Clean seam left for elicitation (drop the enum filter, swap the refusal for an elicit). 22 tests. *(worktree; not yet on master)* Design (source): **Gated generic MCP tool** `run_custom_command(name, video_ids[], mode?)` — one tool with a `name` enum generated from the registry at `list_tools()` time. Preferred over one-tool-per-command because **some clients ignore `tools/list_changed`** (e.g. a reported Claude Desktop gap), so a single generic tool degrades gracefully. **Default OFF** behind `mcp.allow_custom_commands` (LLM-driven shell exec).
- **D.1 — Overlays don't take focus / render (reported 2026-07-02, "never resolved") — RESOLVED.** Turned out to be TWO different failure modes (the pilot harness, Tier-1 #2, diagnosed both by booting the app and measuring widget regions). **(a) command input invisible → FIXED (commit 3413188, by the fable-cmdinput agent on the REAL Textual 8.2.8 runtime).** My first attempt (the off-screen `margin-bottom` layout fix on Textual 6.5.0) was NOT the user's bug — everyone, me included, debugged the wrong Textual. Real cause on 8.x: `Input:focus { border: tall }` inside a `height:1` Input consumes one row top + one bottom → ZERO content rows → the value text is never composited (Input.value fills invisibly, which is exactly why Enter worked and hid it). Plus a white-on-white `.input--cursor` override and `select_on_focus=True` (selected the pre-filled `:`, so the first keystroke replaced it). Fix: `border:none` on the focused input, remove the cursor override, `select_on_focus=False`. Same fix applied to the search input. Verified on 8.2.8: typed text is actually PAINTED (asserted vs the compositor, not `Input.value`). Lesson: debug/verify on the user's runtime, not the venv. **(b) help ignores arrow/j/k → FIXED (ModalScreen).** `HelpOverlay` is now a `ModalScreen` (pushed via `action_help`) that owns the keyboard: focuses its scroll area, scrolls on arrow/j/k/pgup/pgdn/home/end, dismisses on escape/?/q, and `event.stop()`s every key so nothing leaks to the miller view. Removed the embedded widget + dead `show_help` reactive. Both verified with render-aware pilot tests (on-screen region / focus ownership), reviewed PASS (robust down to a 4-row terminal). Commits `8bc8af9`, `e4c3239`. Earlier changelog entry for the first attempt: "TUI overlay bugs (user-reported) — command input visible while typing + help modal owns the keyboard. Command input off-screen fixed via layout (margin-bottom); `HelpOverlay` → `ModalScreen`. +4 render-aware pilot tests. Commits `8bc8af9`, `e4c3239`." (Original diagnosis is kept as prose in the run report below.)
- **D.2 — Tests ran on the WRONG Textual (found by the Tier-1 #2 review) — RESOLVED.** The `.venv` had no `pytest`, so `uv run pytest` silently fell back to homebrew Python 3.10 + **Textual 0.47.1**. **Fixed (commit 9d908aa):** pytest/pytest-asyncio added to `[dependency-groups].dev` (uv installs it by default), so plain `uv run pytest` now runs the venv (Python 3.14 / Textual 6.5.0) — verified. Also directly caused a shipped startup crash: see the guard fix (`_apply_colorscheme`).
- **2.5 — Fix cache-only blind spots.** ✅ DONE. `search_videos` and cross-playlist `find_duplicates` now return a `coverage` field (cached playlists searched/scanned + a note to run list_playlists/get_playlist) so a partial result isn't read as authoritative. +2 tests. *(commit fb5a346)*
- **3.4 — Guard deprecated favorites-playlist modification.** ✅ N/A (verified 2026-07-07). A grep for `favorite` across `src/` is empty: yanger has NO favorites-playlist handling, so no mutation path targets one. Nothing to guard. (Re-open only if favorites support is ever added.)
- **YANGER_CACHE_DIR now actually works + dead `settings.cache.directory` removed (c92f6c3)** — the dead-config reconciliation noted under 3.3; the four-root centralization in 3.3 remains open.
- **Takeout Z-timestamp no-op fixed (6cc03fa)** — the `takeout.py:314` part of 3.2; the rest of 3.2 remains open.
- **Tier 1 · #3 (started) — narrow except.** 2 bare `except:` in takeout + all 14 `operation_history` handlers → `(HttpError, QuotaExceededError)`; bugs now propagate. +2 tests. (Item 1.3 stays active for the ~66-site tail.)
- **1.2 — Tier 1 · #2 — faithful test harness + Textual pilot.** Shared FakeYouTubeAPIClient (real signatures, `inspect`-guarded), a real app-boot Pilot harness driving the confirm modal, ops integration + takeout parsers. +22 tests. Surfaced that the runner was on the wrong Textual. Detail: ✅ DONE (harness). `tests/fakes.py::FakeYouTubeAPIClient` — a shared double with the REAL `YouTubeAPIClient` signatures + a `test_harness.py` **signature-faithfulness guard** (`inspect.signature`) that fails if the fake drifts, banning the `MagicMock(name=...)` false-confidence mode. A **Textual Pilot harness** (`test_pilot_harness.py`) boots the app headless and drives real keystrokes through `ConfirmationModal` (asserts confirm-vs-cancel) — the harness whose absence let the `:run` modal critical through. Integration test drives the real `DeleteVideosOperation` + `OperationStack` through the fake; `takeout.py` content parsers now covered. **Big discovery:** `uv run pytest` was silently on homebrew Textual 0.47.1, not the pinned 6.5.0 — see D.2. *Coverage of the full command layer can grow further, but the harness (the velocity-unlock deliverable) is in.*
- **1.4 — Tier 1 · #4 — shared cross-process API quota.** `quota_used` → property backed by a SQLite `quota_usage` table (migration v2) keyed to the Pacific reset window; TUI/MCP/CLI share one atomic count. `tzdata` dep + lazy/defensive ZoneInfo. +7 tests. Detail: `YouTubeAPIClient.quota_used` is now a property backed by an injected `quota_store` (the SQLite cache): a `quota_usage(reset_key, used)` table (migration v2) keyed to the Pacific-midnight window, atomic UPSERT increment. TUI + MCP (+ the CLI commands) share one running count that auto-resets when the Pacific day rolls over. `tzdata` added as a dep + the ZoneInfo lookup made lazy/defensive (no import-time crash on tz-db-less platforms). Falls back to in-memory when no store is passed (back-compat). *Was Med · S-M · dep: cache (#0.5).*
- **1.5 — Tier 1 · #5 — versioned cache migrations.** `PRAGMA user_version` + ordered idempotent `_MIGRATIONS` replace the no-op schema bump and the ad-hoc per-write `ALTER TABLE`. +4 tests. Detail: Replaced the no-op `SCHEMA_VERSION` branch (advanced the number without running anything) + the ad-hoc `ALTER TABLE`s (which lived in the hot `update_virtual_video_metadata` write path) with `PRAGMA user_version` + an ordered `_MIGRATIONS` tuple applied once at init. Steps are individually idempotent (guarded add-if-absent) since Python's sqlite3 auto-commits DDL — documented that they're NOT rolled back as a unit. Legacy dbs (user_version 0) converge correctly. *Was Med, S-M.*
- **1.1 — Tier 1 · #1 — transcript fetch+cache unified.** 4 write paths → one injected-cache `fetch_and_cache_transcript` service; read gate → one `should_refetch`. Kills the duplication class behind §0; mcp `get_transcript` now caches `NOT_AVAILABLE` + recovers legacy poisoned rows. Net −118/+151 (the +151 is mostly tests). +15 tests, suite 238 green. Dual-reviewed PASS. Detail: All 4 hand-copied write bodies (app auto/manual, mcp get/batch) now delegate to `core/transcript_fetcher.fetch_and_cache_transcript(fetcher, cache, video_id)` — the single owner of format/compress/cache + the terminal-vs-transient policy (cache **injected**, so `core` stays a leaf, no cycle). The **read**-side "should I refetch?" gate is single-sourced too via `should_refetch(status)` (tracks `TERMINAL_TRANSCRIPT_STATUSES`), folded into all 3 refetch sites (TUI auto-fetch + mcp `get_transcript` cache-first + mcp `batch skip_cached`) — MCP now also recovers legacy poisoned transient rows. Bonus: mcp `get_transcript` now caches `NOT_AVAILABLE` (previously refetched forever). The `!= "SUCCESS"` **serve**-checks (mcp:1011, mcp search, app export, miller_view) are a *different* question ("do I have a servable body?") and are intentionally left alone. +15 tests (`test_transcript_service.py`). *Was Impact High, M.*
- **H.2 — ★ Headline slice 2 — long-form commands + batch.** `commands:` now accepts `{run, mode: per-video|batch, confirm: true}`; batch runs one `{urls}`/`{ids}` invocation over the selection; `confirm: true` always prompts. +12 tests. Reviewed PASS (batch quoting = shlex.join-safe). Scope (source): long-form `{run, mode, confirm}` dict (mode validated; unknown → per-video); **batch** mode (`{urls}`/`{ids}` argv, `shlex`-quoted = `shlex.join`-safe, confirmed on large selections to guard ARG_MAX); `confirm: true` always prompts. +12 tests, review PASS (batch injection posture verified equivalent to per-video).
- **H.1 — ★ Headline v1 — custom-command registry + `:run`.** YAML `commands: {name: template}` + `YANGER_CMD_<NAME>` env → `:run <name>` on marked-else-current, per-video, with a confirm gate over 5 videos. Core builder/runner shared with `:transcript` (delegation, no copy). Fixed a critical caught in review: `ConfirmationModal` was keyboard-inoperable when `dangerous=False` (`y` now confirms any modal). +31 tests (core 13, `:run` 13, modal 5). Deferred to slice 1b/2: long-form dict, batch, `{title}`, `:set` persistence, MCP tool. Agreed v1 scope (this /loop run, post adversarial-critic): YAML `commands: {name: "template"}` (bare strings) + `YANGER_CMD_<NAME>` env overrides; keys normalized lowercase. `:run <name>` on marked-else-current (mirrors `dd`/`yy`), **per-video only**. Generic `build_command`/`run_command` live in `core/custom_command.py`; `core/transcript_command.py` **delegates** to them (dedup, not a hand-copy). Confirm modal when selection > threshold. `:run`/`:run <unknown>` surface available names.
- **Tier 0 · 0.1/0.2/0.7/0.8/0.11 — docs + UX + dead-code.** README `sync`/`proxy` sections; `.env.example` proxy vars; `gR` refresh-all alias (dropped the undeliverable Ctrl+Shift+R binding); deleted dead `BulkEditExecutor` (coverage repointed to `BulkEditOperation`); `colorscheme` → Textual native `App.theme` (floor `textual>=0.86`). +6 tests. 0.12 deferred.
- **0.1** README: document `sync` + `proxy` — Marquee CLI features undocumented (`cli.py:544,954`).
- **0.2** `.env.example`: add proxy vars — `proxy status` advertises them (`cli.py:991`).
- **0.7** `Ctrl+Shift+R` → reachable key (e.g. `gR`) — Terminals can't deliver the chord; binding is dead (`keybindings.py`/README).
- **0.8** Delete dead `BulkEditExecutor` + repoint its test — False-confidence duplicate apply path (`bulkedit.py:323,434`).
- **0.11** Wire `colorscheme` → Textual native themes — Closes dead `colorscheme` string; free `ctrl+p` "Change theme" (`config/settings.py:20`, `app.py:51`). Note: wired `colorscheme` → Textual's native `App.theme` on the *current* Textual (floor raised to `>=0.86`, the version that added the theme system); deliberately did **not** do the risky 6.5→8.x major bump. `colorscheme: nord` etc. now works; unknown/`default` keep the built-in theme.
- **Tier 0 · 0.4/0.9 — None-title crash fixes + non-zero exit codes.** `Video.__post_init__` coerces None title/channel/description → "" (fixes duplicates/statistics/export + two MCP paths); `proxy test` and `fetch-metadata` now `sys.exit(1)` on error. +9 tests.
- **0.4** `None`-title/description crash fixes — Crashes duplicates/statistics/`export --csv` on pre-metadata videos.
- **0.9** `fetch-metadata` / `proxy test` non-zero exit on error — Exit 0 breaks scripting/CI (`cli.py`).
- **Tier 0 · 0.3/0.5/0.6/0.10 — CLI/cache hardening.** `reset` now targets the real paths (`~/.cache/yanger`, `~/.config/yanger/config.yaml`, resolved token) with confirm guards + `--yes`; cache `_connect` enables WAL + `busy_timeout=5000`; `--verbose` wired into `ctx.obj`; transcript-api pin → `>=1.2`. +7 tests. Introduced shared `cache.default_cache_dir()`.
- **0.3** `yanger reset`: fix wrong paths + add confirm — Silently no-ops on real cache/config; unguarded destructive (`cli.py:166,182,193`).
- **0.5** `journal_mode=WAL` + `busy_timeout` in `_connect()` — MCP off-loop writes made `database is locked` reachable (`cache.py:66-75`).
- **0.6** `-v/--verbose` → `ctx.obj` — Group sets it but `run` never sees it (dead flag) (`cli.py:50,102`).
- **0.10** Bump `youtube-transcript-api` pin `>=1.2` — Pin (`>=0.6.2`) lags shipped 1.x code (`pyproject.toml`).
- **§0 — transient-transcript cache poisoning fixed.** Hoisted `TERMINAL_TRANSCRIPT_STATUSES` to `core/transcript_fetcher.py`; TUI auto-fetch now caches only terminal statuses. +5 regression tests. _(commit recorded in journal)_ Detail (source §0, "Known issue found during discovery"): `_auto_fetch_transcript` (`src/yanger/app.py`) cached *every* status including transient `IP_BLOCKED`/`ERROR`, permanently poisoning the cache so a later-configured proxy couldn't recover. **Fixed** by hoisting the shared policy `TERMINAL_TRANSCRIPT_STATUSES` into its owner `core/transcript_fetcher.py` (now imported by both `app.py` and `mcp_server.py`) and gating the auto-fetch cache write on it. Regression test: `tests/test_auto_fetch_transcript_caching.py` (5 cases). (Full fetch+cache unification was Tier 1 item 1.)

### Run report — /goal autonomous pass (2026-07-07), kept verbatim

Directive: do the obvious, low-human-input roadmap items; skip the rest. On the
`worktree-roadmap-knockout` branch (NOT merged to master pending human approval).

> **Done this pass:** YANGER_CACHE_DIR now actually works + dead `settings.cache.directory`
> removed (c92f6c3); Tier-2 cache-blind-spot coverage annotation (fb5a346); Tier-3 guard-favorites
> verified N/A; `pytest` in the uv dev-group so `uv run pytest` uses the venv (9d908aa); Takeout
> Z-timestamp no-op fixed (6cc03fa); the test-env "wrong Textual" follow-up resolved; and the
> **command-input "typed text invisible" bug FIXED** by the fable-cmdinput agent — the real cause
> (`Input:focus { border: tall }` clipping content to zero rows + `select_on_focus`) found and
> verified on the user's actual Textual 8.2.8 runtime (commit 3413188).
>
> **Skipped — needs human judgment / too large (per directive):**
> Tier-2 **elicitation**, **resources+prompts**, **sampling**, **MCP undo parity** — real MCP
> features needing design/client-capability decisions.
> (The gated MCP `run_custom_command` tool was then DONE in the resumed loop 2026-07-08 — see H.3.)
> Tier-3 **decompose app.py** (structural), **takeout improvements** (multi-part, wants real
> Takeout data), **full path centralization**, **YAML keybindings** (feature-design).
> Tier-1 **#3 tail** (~66 more `except` sites) — needs per-site failure-mode judgment; not
> mechanically obvious.
> **All 3 "Decisions needed"** — explicitly human product calls (drop the rich-UI branch; drop
> vision features; reconcile CLAUDE.md).

### D.1 original diagnosis, kept verbatim

> Two symptoms, **one root cause**: `CommandInput` and `HelpOverlay` are CSS-`display`-toggled
> `Container`s embedded in the main `compose()` (`app.py:154,165`), NOT `ModalScreen`s on the screen
> stack — so the app's `on_key` and the miller view keep keyboard focus behind them.
> **(a) Command input is invisible while typing.** `:`+text captures fine (Enter submits → the
> ack/error popup you see), but the typed text never renders. `ui/command_input.py` is littered
> with `!important` colour overrides (`color: white !important`, forced styles in `show()`) — the
> fingerprint of an unwon rendering fight. *Severe: users can't see what they type.*
> **(b) Help overlay ignores arrow/`j`/`k`.** `app.py:on_key` returns early when the command input
> (`:1112`) or search input (`:1119`) has focus, but has **no check for `help_overlay`**, so
> navigation keys fall through to the miller columns behind it (`:1140+`). `HelpOverlay.on_key`
> (`help_overlay.py:205`) only handles `escape`/`?`, never scroll. Mouse wheel works (routed to the
> widget under the pointer, bypassing `on_key`).
> **Fix (both):** convert `CommandInput` + `HelpOverlay` to `ModalScreen`s pushed on the stack — the
> pattern `ui/confirmation_modal.py` already uses and the new pilot harness (`test_pilot_harness.py`)
> can verify. That yields focus capture, key isolation from the background, and correct top-layer
> rendering, and lets the `!important` CSS hacks be deleted. *Cheaper interim fix for (b) alone: add
> a `help_overlay`-focus early-return in `on_key` and give `HelpOverlay.on_key` up/down handling.*
> *Impact High (usability), Effort M, verifiable with the Tier-1 #2 pilot.*
