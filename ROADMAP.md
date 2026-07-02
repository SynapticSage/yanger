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

1. **Unify transcript fetch+cache into one core service.** Collapse the 4 hand-copied
   copies (`app.py:966`, `app.py:1017`, `mcp_server.py:995`, `mcp_server.py:1418`) into a
   single `fetch_and_cache_transcript(...)` owning the format/compress/cache + terminal-vs-transient
   policy. §0 already hoisted the **write**-side policy constant `TERMINAL_TRANSCRIPT_STATUSES`
   into `core/transcript_fetcher.py`; the remaining work is (a) the four fetch+cache bodies and
   (b) the **read**-side "is-terminal ⇒ don't retry" gate, still triplicated and inconsistent:
   `app.py:1335` (hardcoded `['SUCCESS','NOT_AVAILABLE']` — won't track the constant if it grows),
   `mcp_server.py:1004-1012` and `:1443-1447` (treat *any* cached row as terminal). The service
   should own both sides and fold `app.py:1335` onto the shared constant. **Inject the cache
   handle** into the service (don't import `PersistentCache` into `core` — keeps `core` a leaf,
   avoids a cache⇄transcript_fetcher cycle). *Impact High · Effort M · no dep.*
2. **Faithful API-client/cache test harness + cover untested paths.** One shared test
   double for `YouTubeAPIClient` with real method signatures (ban per-method
   `MagicMock(name=...)`); integration tests driving `execute_command`/operations against
   the double + a real temp SQLite cache. Cover `api_client.py`, the command layer,
   `takeout.py`/`duplicates.py`/`statistics.py` (currently zero direct tests).
   *Impact High · Effort M-L · no dep · velocity unlock for every later refactor.*
3. **Narrow `except Exception`** project-wide (80 occurrences + 2 bare). Start with
   `operation_history.py` (14) and the api_client-calling handlers; catch real modes
   (`HttpError`, `sqlite3.Error`, `OSError`, `CalledProcessError`) and let
   `AttributeError`/`TypeError` propagate. *Impact Med-High · Effort M · best after #2.*
4. **Persist + share quota across processes.** `quota_used` resets per process and is
   reported as "remaining today" (`api_client.py:54`, `mcp_server.py:1079`); TUI and MCP
   don't share it. Persist in SQLite keyed to the YouTube Pacific-midnight reset window.
   *Impact Med · Effort S-M · dep: cache (#0.5).*
5. **Versioned migration framework.** Replace the no-op `SCHEMA_VERSION` branch
   (`cache.py:179`) + ad-hoc `ALTER TABLE`s with `PRAGMA user_version` + ordered,
   transactional steps. *Impact Med · Effort S-M.*

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
