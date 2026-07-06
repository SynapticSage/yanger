# YouTube Ranger (yanger) — Roadmap

Status: **alpha**. This roadmap was synthesized from a three-lens discovery pass
(gap-analysis + emerging opportunities, architecture/tech-debt, product/features)
against the merged codebase. Items are proposals, prioritized by leverage.

**Legend** — Impact = user-visible value (High / Med / Low). Effort = S (≤ ~½ day) ·
M (~1–3 days) · L (> 3 days). "Velocity unlock" = makes later work cheaper/safer.

## Guiding theme

yanger's biggest risk is not missing features — it's **duplication on untested
paths**. Both prior Criticals lived on zero-coverage code, and two tests were
*masking* bugs by mocking nonexistent method names. The transcript fetch+cache
logic is hand-copied in four places and has already diverged into a live bug (see
below). A few foundational consolidations make every subsequent feature — including
the headline custom-command registry — cheaper and safer to build.

---

## Discovered during /loop (follow-ups)

- **✅ RESOLVED — Overlays don't take focus / render (reported 2026-07-02, "never resolved").**
  Turned out to be TWO different failure modes (the pilot harness, Tier-1 #2, diagnosed both by
  booting the app and measuring widget regions):
  - **(a) command input invisible → FIXED (layout, not CSS).** The `Input` was composited
    *off-screen* (rows 30-31 on a 30-row terminal): `#command-input` and `#status-bar` both
    `dock: bottom` collided, and an internal `margin-top` pushed the Input below the viewport.
    Fix: `#command-input` `margin-bottom: 1` (sit above the status bar) + drop the Input `margin-top`
    / container `border-top`. The wall of `!important` colour CSS was treating the wrong disease.
  - **(b) help ignores arrow/j/k → FIXED (ModalScreen).** `HelpOverlay` is now a `ModalScreen`
    (pushed via `action_help`) that owns the keyboard: focuses its scroll area, scrolls on
    arrow/j/k/pgup/pgdn/home/end, dismisses on escape/?/q, and `event.stop()`s every key so nothing
    leaks to the miller view. Removed the embedded widget + dead `show_help` reactive.
  - Both verified with render-aware pilot tests (on-screen region / focus ownership), reviewed PASS
    (robust down to a 4-row terminal). Commits `8bc8af9`, `e4c3239`. _Original diagnosis kept below._

  Two symptoms, **one root cause**: `CommandInput` and `HelpOverlay` are CSS-`display`-toggled
  `Container`s embedded in the main `compose()` (`app.py:154,165`), NOT `ModalScreen`s on the screen
  stack — so the app's `on_key` and the miller view keep keyboard focus behind them.
  - **(a) Command input is invisible while typing.** `:`+text captures fine (Enter submits → the
    ack/error popup you see), but the typed text never renders. `ui/command_input.py` is littered
    with `!important` colour overrides (`color: white !important`, forced styles in `show()`) — the
    fingerprint of an unwon rendering fight. *Severe: users can't see what they type.*
  - **(b) Help overlay ignores arrow/`j`/`k`.** `app.py:on_key` returns early when the command input
    (`:1112`) or search input (`:1119`) has focus, but has **no check for `help_overlay`**, so
    navigation keys fall through to the miller columns behind it (`:1140+`). `HelpOverlay.on_key`
    (`help_overlay.py:205`) only handles `escape`/`?`, never scroll. Mouse wheel works (routed to the
    widget under the pointer, bypassing `on_key`).
  - **Fix (both):** convert `CommandInput` + `HelpOverlay` to `ModalScreen`s pushed on the stack — the
    pattern `ui/confirmation_modal.py` already uses and the new pilot harness (`test_pilot_harness.py`)
    can verify. That yields focus capture, key isolation from the background, and correct top-layer
    rendering, and lets the `!important` CSS hacks be deleted. *Cheaper interim fix for (b) alone: add
    a `help_overlay`-focus early-return in `on_key` and give `HelpOverlay.on_key` up/down handling.*
    *Impact High (usability) · Effort M · verifiable with the Tier-1 #2 pilot.*

- **⚠️ Tests ran on the WRONG Textual for most of this run (found by the Tier-1 #2 review).**
  The `.venv` has no `pytest`, so `uv run pytest` silently falls back to homebrew Python 3.10 +
  **Textual 0.47.1** — 6 majors below the pinned `textual>=0.86` (venv has 6.5.0). The suite passed
  on both, but Textual-version-sensitive code was validated on an unsupported framework. **Fix: use
  `uv run --extra dev pytest`** (runs the venv/6.5.0). Consider adding pytest to the venv or a
  Makefile/tox target so the correct runner is the default. (Project memory updated.)

- **Bulk-edit renames are silently dropped but reported as done.** (Surfaced by the 0.8
  arch review.) The live apply path `operation_history.BulkEditOperation.execute` never
  iterates `changes.renames`, yet the preview renders a "Renames" section, `summary()` counts
  them, and the success toast says "Bulk edit completed" — so a user who renames an item in the
  bulk editor is told it worked while nothing happened. (YouTube API can't rename playlist
  *items*; playlist/video title renames go through `cw`.) *Fix: either drop renames from the
  bulk-edit preview/summary, or surface them as an explicit "not applied (unsupported)" result.
  Small · UX-correctness.*

## Changelog

Completed items land here (newest first) with the commit that shipped them. Full
per-run detail lives in the gitignored `journal/`.

- **TUI overlay bugs (user-reported) — command input visible while typing + help modal owns the
  keyboard.** Command input off-screen fixed via layout (margin-bottom); `HelpOverlay` → `ModalScreen`.
  +4 render-aware pilot tests. Commits `8bc8af9`, `e4c3239`.
- **Tier 1 · #3 (started) — narrow except.** 2 bare `except:` in takeout + all 14
  `operation_history` handlers → `(HttpError, QuotaExceededError)`; bugs now propagate. +2 tests.
- **Tier 1 · #2 — faithful test harness + Textual pilot.** Shared FakeYouTubeAPIClient (real
  signatures, `inspect`-guarded), a real app-boot Pilot harness driving the confirm modal, ops
  integration + takeout parsers. +22 tests. Surfaced that the runner was on the wrong Textual.
- **Tier 1 · #4 — shared cross-process API quota.** `quota_used` → property backed by a SQLite
  `quota_usage` table (migration v2) keyed to the Pacific reset window; TUI/MCP/CLI share one
  atomic count. `tzdata` dep + lazy/defensive ZoneInfo. +7 tests.
- **Tier 1 · #5 — versioned cache migrations.** `PRAGMA user_version` + ordered idempotent
  `_MIGRATIONS` replace the no-op schema bump and the ad-hoc per-write `ALTER TABLE`. +4 tests.
- **Tier 1 · #1 — transcript fetch+cache unified.** 4 write paths → one injected-cache
  `fetch_and_cache_transcript` service; read gate → one `should_refetch`. Kills the duplication
  class behind §0; mcp `get_transcript` now caches `NOT_AVAILABLE` + recovers legacy poisoned rows.
  Net −118/+151 (the +151 is mostly tests). +15 tests, suite 238 green. Dual-reviewed PASS.
- **★ Headline slice 2 — long-form commands + batch.** `commands:` now accepts
  `{run, mode: per-video|batch, confirm: true}`; batch runs one `{urls}`/`{ids}` invocation over the
  selection; `confirm: true` always prompts. +12 tests. Reviewed PASS (batch quoting = shlex.join-safe).
- **★ Headline v1 — custom-command registry + `:run`.** YAML `commands: {name: template}` +
  `YANGER_CMD_<NAME>` env → `:run <name>` on marked-else-current, per-video, with a
  confirm gate over 5 videos. Core builder/runner shared with `:transcript` (delegation, no
  copy). Fixed a critical caught in review: `ConfirmationModal` was keyboard-inoperable when
  `dangerous=False` (`y` now confirms any modal). +31 tests (core 13, `:run` 13, modal 5).
  Deferred to slice 1b/2: long-form dict, batch, `{title}`, `:set` persistence, MCP tool.
- **Tier 0 · 0.1/0.2/0.7/0.8/0.11 — docs + UX + dead-code.** README `sync`/`proxy` sections;
  `.env.example` proxy vars; `gR` refresh-all alias (dropped the undeliverable Ctrl+Shift+R
  binding); deleted dead `BulkEditExecutor` (coverage repointed to `BulkEditOperation`);
  `colorscheme` → Textual native `App.theme` (floor `textual>=0.86`). +6 tests. 0.12 deferred.
- **Tier 0 · 0.4/0.9 — None-title crash fixes + non-zero exit codes.** `Video.__post_init__`
  coerces None title/channel/description → "" (fixes duplicates/statistics/export + two MCP paths);
  `proxy test` and `fetch-metadata` now `sys.exit(1)` on error. +9 tests.
- **Tier 0 · 0.3/0.5/0.6/0.10 — CLI/cache hardening.** `reset` now targets the real paths
  (`~/.cache/yanger`, `~/.config/yanger/config.yaml`, resolved token) with confirm guards + `--yes`;
  cache `_connect` enables WAL + `busy_timeout=5000`; `--verbose` wired into `ctx.obj`; transcript-api
  pin → `>=1.2`. +7 tests. Introduced shared `cache.default_cache_dir()`.
- **§0 — transient-transcript cache poisoning fixed.** Hoisted `TERMINAL_TRANSCRIPT_STATUSES`
  to `core/transcript_fetcher.py`; TUI auto-fetch now caches only terminal statuses. +5 regression
  tests. _(commit recorded in journal)_

---

## 0. Known issue found during discovery (fix outside the roadmap)

- ✅ **RESOLVED — TUI auto-fetch caches transient transcript failures.** `_auto_fetch_transcript`
  (`src/yanger/app.py`) cached *every* status including transient `IP_BLOCKED`/`ERROR`,
  permanently poisoning the cache so a later-configured proxy couldn't recover. **Fixed** by
  hoisting the shared policy `TERMINAL_TRANSCRIPT_STATUSES` into its owner
  `core/transcript_fetcher.py` (now imported by both `app.py` and `mcp_server.py`) and gating
  the auto-fetch cache write on it. Regression test: `tests/test_auto_fetch_transcript_caching.py`
  (5 cases). See Changelog. (Full fetch+cache unification remains Tier 1 item 1.)

---

## ★ Headline track — User-defined custom-command registry (+ agent-domain integration)

> **Confirmed top priority (user request, 2026-07-02):** "a config file where users can set
> custom command names to bash commands or pipes of commands to run on a video URL." That is
> exactly this track — a YAML `commands:` registry mapping names → shell templates/pipes with
> `{url}`/`{id}` placeholders, run against the current video or a selection. No separate item is
> needed; this headline *is* the feature. Build the **core registry + `:run`** slice first.

Generalize the existing single `:transcript` hook into a **named registry of shell
commands** users can run on the current video *or* a selection. Pure reuse of
`src/yanger/core/transcript_command.py` (`build_command`/`resolve`/`run`, `shell=True`,
`shlex.quote`'d `{url}`/`{id}`) and existing selection helpers
(`miller_view.video_column.get_marked_videos()`, visual mode) + config plumbing
(`save_user_setting`, env merge in `load_settings`).

### Config schema
```yaml
# ~/.config/yanger/config.yaml
commands:
  dl:    "yt-dlp {url}"
  sum:   "yeet {url} | fabric -sp summarize"
  archive: "archivebox add {url}"
  # long form opts into batch + confirmation:
  dlall: { run: "yt-dlp {urls}", mode: batch, confirm: true }
```
- Bare `name: "template"` ⇒ `{run: template, mode: per-video, confirm: false}`.
- Env override convention `YANGER_CMD_<NAME>` (mirrors `YANGER_TRANSCRIPT_COMMAND`);
  add a `commands` branch to `_USER_SETTING_SECTIONS` so `:set` can persist them.
- `transcript_command` becomes the reserved default-named command → **back-compat,
  no migration**.

### Placeholders & set-semantics
- Single video: `{url}`, `{id}`, plus `{title}` (filenames).
- Selection — two modes, chosen per-command via `mode:`:
  - **`per-video`** (default): run once per selected video (each `shlex.quote`'d).
  - **`batch`**: one invocation with `{urls}`/`{ids}` (space-joined, quoted) or stdin
    (newline-joined) for `xargs`/`fabric`-style tools.
- Default on a selection with no `mode:` is **per-video** (least surprising).

### TUI invocation UX
- `:run <name>` (new branch in `execute_command`) — operates on marked/visual
  selection if any, else current video (mirrors the `dd`/`yy` marked-else-current
  convention).
- Tab-completion of command names; optional user-bindable keys (`keybindings:` →
  `:run <name>`); optional later: a quick fuzzy command palette.

### Execution & safety
- `shell=True` (user's own config; same posture as `transcript_command`/`$EDITOR`),
  only injected URLs/IDs are `shlex.quote`'d.
- `with self.suspend(): asyncio.to_thread(run)` for interactive/streaming tools.
- Output: per-video interactive streams live; batch/capture → scrollable modal or
  `$PAGER`; non-zero exits surfaced per video.
- **Confirm** modal when selection > threshold (e.g. 5) or `confirm: true`.
- It runs arbitrary user-configured shell — acceptable/user-owned; guardrails:
  confirm-on-large-selection, quote-only-injection, never auto-run on startup,
  document clearly.

### LLM / MCP / Skill integration
- **Gated generic MCP tool** `run_custom_command(name, video_ids[], mode?)` — one tool
  with a `name` enum generated from the registry at `list_tools()` time. Preferred over
  one-tool-per-command because **some clients ignore `tools/list_changed`** (e.g. a
  reported Claude Desktop gap), so a single generic tool degrades gracefully.
  **Default OFF** behind `mcp.allow_custom_commands` (LLM-driven shell exec).
- **Commands as LLM pipelines:** capture command stdout as an **analysis artifact**
  keyed to the video, reusing the transcript/`fabric_analyze` cache → returnable via a
  new `get_analysis`; unify TUI-run and MCP-run results in one store.
- **MCP elicitation** as the confirm-before-shell-exec / before-irreversible gate.
- **MCP sampling** lets pipelines use the *client's* model — no local Fabric/key.
- **Claude Skill / slash-commands** that surface the registry to the agent and can
  **author new commands** into config (with confirmation); Agent-Skills packaging.
- **LLM chaining**: agent composes registry commands across a selection (transcript →
  summarize → file note), mapping onto the per-video/batch semantics.

**Slice priorities:** core registry + `:run` = **High / M / no deps** → generic MCP
tool = High / M (dep: registry + opt-in gate) → analysis-cache wiring = High / S-M →
skill + chaining + prompts = Med / strategic.

**Agreed v1 scope (this /loop run, post adversarial-critic).** The core `:run` slice ships
minimal and safe; everything else is an explicit later slice:
- **v1 (now):** YAML `commands: {name: "template"}` (bare strings) + `YANGER_CMD_<NAME>` env
  overrides; keys normalized lowercase. `:run <name>` on marked-else-current (mirrors `dd`/`yy`),
  **per-video only**. Generic `build_command`/`run_command` live in `core/custom_command.py`;
  `core/transcript_command.py` **delegates** to them (dedup, not a hand-copy). Confirm modal when
  selection > threshold. `:run`/`:run <unknown>` surface available names.
- **✅ slice 2 DONE:** long-form `{run, mode, confirm}` dict (mode validated; unknown → per-video);
  **batch** mode (`{urls}`/`{ids}` argv, `shlex`-quoted = `shlex.join`-safe, confirmed on large
  selections to guard ARG_MAX); `confirm: true` always prompts. +12 tests, review PASS (batch
  injection posture verified equivalent to per-video).
- **Still deferred:** injecting `transcript_command` into the registry (dual-source-of-truth —
  back-compat lives in the untouched `:transcript` branch); batch **stdin** (only argv shipped);
  `{title}` (uploader-controlled → wider injection surface); `:set commands.<name>` persistence
  (users edit `config.yaml` directly); tab-completion; the **gated MCP `run_custom_command` tool**
  (wants the Tier-2 opt-in/elicitation gate).
- **Note:** `Settings.commands` must be wired at all **four** touch-points (field, `from_dict`,
  `merge`'s section list, `save_settings`) or `commands:` is silently dropped.

---

## Tier 0 — Quick wins (S effort, clear value, ship first)

| # | Item | Why | Evidence |
|---|---|---|---|
| ✅ 0.1 | README: document `sync` + `proxy` | Marquee CLI features undocumented | `cli.py:544,954` |
| ✅ 0.2 | `.env.example`: add proxy vars | `proxy status` advertises them | `cli.py:991` |
| ✅ 0.3 | `yanger reset`: fix wrong paths + add confirm | Silently no-ops on real cache/config; unguarded destructive | `cli.py:166,182,193` |
| ✅ 0.4 | `None`-title/description crash fixes | Crashes duplicates/statistics/`export --csv` on pre-metadata videos | duplicates/statistics/export |
| ✅ 0.5 | `journal_mode=WAL` + `busy_timeout` in `_connect()` | MCP off-loop writes made `database is locked` reachable | `cache.py:66-75` |
| ✅ 0.6 | `-v/--verbose` → `ctx.obj` | Group sets it but `run` never sees it (dead flag) | `cli.py:50,102` |
| ✅ 0.7 | `Ctrl+Shift+R` → reachable key (e.g. `gR`) | Terminals can't deliver the chord; binding is dead | `keybindings.py`/README |
| ✅ 0.8 | Delete dead `BulkEditExecutor` + repoint its test | False-confidence duplicate apply path | `bulkedit.py:323,434` |
| ✅ 0.9 | `fetch-metadata` / `proxy test` non-zero exit on error | Exit 0 breaks scripting/CI | `cli.py` |
| ✅ 0.10 | Bump `youtube-transcript-api` pin `>=1.2` | Pin (`>=0.6.2`) lags shipped 1.x code | `pyproject.toml` |
| ✅ 0.11 | Wire `colorscheme` → Textual native themes | Closes dead `colorscheme` string; free `ctrl+p` "Change theme" | `config/settings.py:20`, `app.py:51` |
| ⏸️ 0.12 | Status-bar contextual hint line | Discoverability (the CLAUDE.md mock shows it) | `ui/status_bar.py` |

> **0.11 note:** wired `colorscheme` → Textual's native `App.theme` on the *current* Textual
> (floor raised to `>=0.86`, the version that added the theme system); deliberately did **not** do
> the risky 6.5→8.x major bump. `colorscheme: nord` etc. now works; unknown/`default` keep the
> built-in theme.
> **0.12 DEFERRED:** a hint line already exists (`status_bar.update_hints` shows `yy:copy dd:cut
> pp:paste`, matching the CLAUDE.md mock). Making it *contextual* requires resolving the shared
> center-widget status/hint contention — a UX refactor out of proportion to a "quick win" and
> regression-prone. Left for a dedicated UI pass.

---

## Tier 1 — Foundations (do before/with the headline; unlock velocity + safety)

1. ✅ **DONE — Unify transcript fetch+cache into one core service.** All 4 hand-copied write
   bodies (app auto/manual, mcp get/batch) now delegate to
   `core/transcript_fetcher.fetch_and_cache_transcript(fetcher, cache, video_id)` — the single
   owner of format/compress/cache + the terminal-vs-transient policy (cache **injected**, so
   `core` stays a leaf, no cycle). The **read**-side "should I refetch?" gate is single-sourced
   too via `should_refetch(status)` (tracks `TERMINAL_TRANSCRIPT_STATUSES`), folded into all 3
   refetch sites (TUI auto-fetch + mcp `get_transcript` cache-first + mcp `batch skip_cached`) —
   MCP now also recovers legacy poisoned transient rows. Bonus: mcp `get_transcript` now caches
   `NOT_AVAILABLE` (previously refetched forever). The `!= "SUCCESS"` **serve**-checks (mcp:1011,
   mcp search, app export, miller_view) are a *different* question ("do I have a servable body?")
   and are intentionally left alone. +15 tests (`test_transcript_service.py`). *Was Impact High · M.*
2. ✅ **DONE (harness) — Faithful API-client/cache test harness + a real TUI pilot.**
   `tests/fakes.py::FakeYouTubeAPIClient` — a shared double with the REAL `YouTubeAPIClient`
   signatures + a `test_harness.py` **signature-faithfulness guard** (`inspect.signature`) that
   fails if the fake drifts, banning the `MagicMock(name=...)` false-confidence mode. A
   **Textual Pilot harness** (`test_pilot_harness.py`) boots the app headless and drives real
   keystrokes through `ConfirmationModal` (asserts confirm-vs-cancel) — the harness whose absence
   let the `:run` modal critical through. Integration test drives the real `DeleteVideosOperation`
   + `OperationStack` through the fake; `takeout.py` content parsers now covered. +22 tests.
   **Big discovery:** `uv run pytest` was silently on homebrew Textual 0.47.1, not the pinned
   6.5.0 — see "Discovered during /loop". *Coverage of the full command layer can grow further,
   but the harness (the velocity-unlock deliverable) is in.*
3. 🔶 **STARTED — Narrow `except Exception`.** Done: both **bare `except:`** in `takeout.py`
   → `(ValueError, TypeError)`; and all **14 handlers in `operation_history.py`** → a shared
   `_OPERATION_API_ERRORS = (HttpError, QuotaExceededError)` tuple, so a real API/quota error is a
   clean `return False` while a bug (AttributeError/KeyError from a stale id) propagates instead of
   being masked. +2 harness tests prove both behaviors (built on the #2 fake). **Remaining (~66
   sites in app/mcp/cli/etc.):** need per-site failure-mode analysis — file I/O → `OSError`,
   subprocess → `CalledProcessError`, JSON → `JSONDecodeError`, SQLite → `sqlite3.Error` — NOT a
   uniform sweep. *Impact Med-High · Effort M · continue on the #2 safety net.*
4. ✅ **DONE — Persist + share quota across processes.** `YouTubeAPIClient.quota_used` is now a
   property backed by an injected `quota_store` (the SQLite cache): a `quota_usage(reset_key, used)`
   table (migration v2) keyed to the Pacific-midnight window, atomic UPSERT increment. TUI + MCP
   (+ the CLI commands) share one running count that auto-resets when the Pacific day rolls over.
   `tzdata` added as a dep + the ZoneInfo lookup made lazy/defensive (no import-time crash on
   tz-db-less platforms). Falls back to in-memory when no store is passed (back-compat). +7 tests.
   *Was Med · S-M · dep: cache (#0.5).*
5. ✅ **DONE — Versioned migration framework.** Replaced the no-op `SCHEMA_VERSION` branch
   (advanced the number without running anything) + the ad-hoc `ALTER TABLE`s (which lived in
   the hot `update_virtual_video_metadata` write path) with `PRAGMA user_version` + an ordered
   `_MIGRATIONS` tuple applied once at init. Steps are individually idempotent (guarded
   add-if-absent) since Python's sqlite3 auto-commits DDL — documented that they're NOT rolled
   back as a unit. Legacy dbs (user_version 0) converge correctly. +4 tests. *Was Med · S-M.*

---

## Tier 2 — MCP / LLM surface deepening

- **Elicitation: confirm-before-irreversible** mutations (and before shell-exec).
  Directly de-risks the ALPHA-irreversibility warning. *High · M.*
- **MCP undo parity.** Route MCP mutations through `operation_history` (the TUI has full
  undo/redo; MCP currently records nothing). Pairs with persisted store (#1.4/#1.5).
  *Med-High · M.*
- **Resources + Prompts.** Expose playlists, cached transcripts, and saved commands as
  MCP resources; Fabric patterns as MCP prompts (the explicitly-deferred
  `docs/MCP_SERVER_PLAN.md` Phase 4). *Med · M.*
- **Sampling** for LLM pipelines using the connected client's model (removes the hard
  local-Fabric/key dependency in `fabric_analyze`). *Med · M.*
- **Fix cache-only blind spots.** `search_videos` and cross-playlist `find_duplicates`
  only scan cached playlists and silently return partial results — prime the cache or
  annotate coverage. *Med · M.*

---

## Tier 3 — Strategic / larger

- **Decompose `app.py` (2,690 lines).** Extract the 185-line command dispatcher
  (`execute_command`) + ranger handlers → a `commands/` module; move transcript/metadata
  domain ops → `core/` (realizes Tier-1 #1). Shrinks the god-object, eases testing,
  realigns to the CLAUDE.md structure. *Med · L · after #1.2 · velocity unlock.*
- **Takeout improvements.** JSON history import (preserve watched-at timestamps; current
  HTML parse only recovers IDs; fix the `takeout.py:314` `Z`-timestamp no-op); zip-import
  metadata enrichment; `sync` resume/download-only mode (watch for the emailed zip
  instead of re-submitting an export); validate the live Takeout DOM against a real
  account. *Med · M.*
- **Centralize path resolution.** Four roots in use (`~/.config/yanger`, `~/.cache/yanger`,
  `~/.yanger`, a `.cache/yanger` config default). One XDG-style module (config/cache/state)
  consumed by everything incl. `reset`. *Low-Med · S.* **Note (0.3 review):** `settings.cache.directory`
  (default `.cache/yanger`, overridable via `YANGER_CACHE_DIR`/`config.yaml`) is **dead config** —
  assigned but never passed into any `PersistentCache(...)`, which is why 0.3's `default_cache_dir()`
  is correct today. Centralization must reconcile this: either wire the setting through (and have
  `reset` consume the same resolver) or delete it. Until then it's a latent divergence trap.
- **Guard deprecated favorites-playlist modification** — ensure no mutation path targets a
  "favorites" virtual playlist (YouTube API deprecation). *Low · S.*
- **User-editable keybindings (YAML loader)** — the registry is hardcoded; add a
  `config/keybindings.yaml`. *Med · M.*

---

## Dropped — won't build (explicit product decisions)

- **`:play` external player (mpv/vlc) — DROPPED (2026-07-02, user decision).** Piping YouTube
  video streams into an external player (mpv/vlc, typically via `yt-dlp`) to *watch* videos
  sits crosswise to YouTube's Terms of Service (playback outside the YouTube player / embedded
  player). yanger stays an *organization/management* tool. Users who want this can still wire it
  themselves through the general custom-command registry (their own config, their own posture) —
  we simply don't ship or endorse a first-class `:play` verb. `r` (open in browser) remains the
  supported "watch" affordance. *Note: this does not restrict the custom-command registry, which
  is content-agnostic shell the user configures.*

## Decisions needed

1. **`origin/claude/simple-rich-ui-8ZHYs` → DROP.** A parallel, untested second UI (a
   584-line `rich` menu) re-implements auth + loading independently of `app.py` — the exact
   untested-duplicate pattern behind prior Criticals; no documented need. The CLI + MCP
   already are the non-TUI surface. If accessibility/fallback ever becomes a real reported
   need, add a `--no-tui` mode over the existing CLI verbs (with tests), not a third UI.
2. **Explicitly drop/defer low-fit vision features** so they stop reading as perpetual TODO:
   macros (`qa`/`@a`), plugin system, smart/auto playlists, playlist sharing, merge/split as
   a user op. They're large scope and off the app's actual momentum (TUI + MCP/LLM).
3. **Reconcile the drifted `CLAUDE.md` vision** with reality (or rewrite it): it describes a
   `commands/`/`core/` package layout, `core/` classes, and `api_key` auth that don't exist,
   and `v`/`V`/`gr`/`gR`/`gs`/`H`/`L` keys that differ from the shipped keymap.

---

## Suggested sequencing

- **Now:** §0 live-bug fix + Tier 0 quick wins.
- **Next:** Tier 1 #1 (transcript service) + #2 (test harness) — these unblock everything else.
- **Then:** the headline custom-command registry (core `:run` first; the gated MCP tool once
  elicitation lands).
- **Ongoing:** Tier 2 MCP deepening; Tier 3 once foundations are in.

---

## Appendix A — Built vs intended (vs the CLAUDE.md vision)

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

## Appendix B — External opportunities (as of mid-2026) & references

Installed: `mcp 1.25.0`, `textual 6.5.0`, `youtube-transcript-api 1.2.3`,
`google-api-python-client 2.187.0`.

- **Textual** native theme system (`App.theme` + `register_theme`, since 0.86) and the
  `ctrl+p` command palette; latest is 8.x — closes the colorscheme gap cheaply.
- **MCP spec** (current stable 2025-11-25) adds **elicitation**, **sampling**, structured
  tool output, and resources/prompts — yanger's server is tools-only today. Dynamic tool
  registration via `notifications/tools/list_changed` exists but **client support is uneven**
  (design a generic `run_command` fallback).
- **Claude Skills** (custom slash-commands merged into Skills) are the distribution path for
  a command registry; latest Claude line (Opus 4.8) + Tool Search Tool / programmatic tool
  calling keep large tool libraries usable.
- **Transcripts:** cloud-IP blocking remains the dominant failure mode; Webshare requires
  *rotating Residential* proxies (document this); PoToken support is in progress; a hosted
  fallback can ride the `transcript_command` hook.
- **YouTube Data API v3:** stable (10k/day quota); no uploads so the `videos.insert` cost
  hike is irrelevant; favorites-playlist modification is deprecated (guard it).

References:
- Textual: changelog · command-palette guide (textual.textualize.io)
- MCP: modelcontextprotocol.info/specification · tools spec · transports-future blog · client-capability-gap (pulsemcp)
- Claude: code.claude.com/docs/en/skills · anthropic.com/engineering/advanced-tool-use · platform.claude.com programmatic-tool-calling
- Transcripts: pypi.org/project/youtube-transcript-api · jdepoix/youtube-transcript-api#593 (cloud IP blocking)
- YouTube API: developers.google.com/youtube/v3/revision_history

---

*Sources for this roadmap: the project journals (`./journal/`, local/gitignored), the
CLAUDE.md vision spec, `README.md`, `docs/MCP_SERVER_PLAN.md`, and a three-agent discovery
pass over the merged codebase.*
