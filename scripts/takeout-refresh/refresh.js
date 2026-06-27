#!/usr/bin/env node
/**
 * yanger Takeout refresh routine.
 *
 * Attaches to an ALREADY-RUNNING, ALREADY-LOGGED-IN Chrome (via its
 * remote-debugging port) and drives Google Takeout to create a fresh
 * YouTube-only export, then watches the download directory for the resulting
 * zip and prints its path so the Python wrapper can import it.
 *
 * Design constraints (why it looks the way it does):
 *   - We NEVER type a password or touch credentials. We attach to the user's
 *     own authenticated session, so this is "the user driving their own
 *     browser", not a bot impersonating them. This keeps us inside Google's
 *     data-portability lane (Takeout) and away from login bot-detection.
 *   - Takeout is asynchronous: you submit an export, Google generates it in the
 *     background. So the script is two phases: (1) configure + let the human
 *     submit, (2) watch for the download.
 *   - The Takeout DOM is a Polymer app with obfuscated class names, so all
 *     automation here is best-effort and text-anchored. A human always verifies
 *     and clicks the final "Create export" — that confirmation is the safety net
 *     if a selector drifts.
 *
 * IO contract with the Python wrapper:
 *   - All human-facing progress / prompts go to STDERR.
 *   - Exactly one machine-readable JSON line goes to STDOUT at the end, e.g.
 *       {"status":"downloaded","zipPath":"/.../takeout-....zip"}
 *       {"status":"configured"}
 *       {"status":"timeout"}
 *       {"status":"error","message":"..."}
 */

import puppeteer from "puppeteer-core";
import readline from "node:readline";
import { promises as fs } from "node:fs";
import path from "node:path";
import process from "node:process";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (msg) => process.stderr.write(`${msg}\n`);
const emit = (obj) => process.stdout.write(`${JSON.stringify(obj)}\n`);

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith("--")) continue;
    const key = a.slice(2);
    const next = argv[i + 1];
    if (next === undefined || next.startsWith("--")) {
      args[key] = true;
    } else {
      args[key] = next;
      i++;
    }
  }
  return args;
}

function prompt(question) {
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stderr,
  });
  return new Promise((resolve) =>
    rl.question(question, (ans) => {
      rl.close();
      resolve(ans.trim());
    }),
  );
}

/**
 * Click the first element whose visible text matches `text`. Runs inside the
 * page so Polymer's own click handlers fire. Returns true if something matched.
 */
function clickByText(page, text, { exact = true } = {}) {
  return page.evaluate(
    (text, exact) => {
      const want = text.trim().toLowerCase();
      const tags = ["button", "span", "div", "a", "[role='button']"];
      for (const tag of tags) {
        for (const el of document.querySelectorAll(tag)) {
          const t = (el.textContent || "").trim().toLowerCase();
          const hit = exact ? t === want : t.includes(want);
          if (hit) {
            (el.closest("button,[role='button'],a") || el).click();
            return true;
          }
        }
      }
      return false;
    },
    text,
    exact,
  );
}

/**
 * Find the "YouTube and YouTube Music" product row and ensure its checkbox is
 * ticked. Climbs from the label to the nearest ancestor that owns a checkbox.
 */
function ensureYouTubeSelected(page) {
  return page.evaluate(() => {
    const leaves = [...document.querySelectorAll("*")].filter(
      (el) =>
        el.children.length === 0 &&
        /youtube and youtube music/i.test(el.textContent || ""),
    );
    if (!leaves.length) return "not-found";
    let node = leaves[0];
    for (let i = 0; i < 8 && node; i++) {
      const cb = node.querySelector(
        "[role='checkbox'], input[type='checkbox']",
      );
      if (cb) {
        const checked =
          cb.getAttribute("aria-checked") === "true" || cb.checked === true;
        if (!checked) cb.click();
        return checked ? "already-selected" : "selected";
      }
      node = node.parentElement;
    }
    return "no-checkbox";
  });
}

/**
 * Poll the download directory until a completed .zip appears (Chrome writes a
 * .crdownload partial first). Returns the newest matching zip path, or null on
 * timeout. Agnostic to HOW the download started — auto-click or the user
 * clicking the email link both land here.
 */
async function waitForZip(dir, sinceMs, waitMinutes) {
  const deadline = Date.now() + waitMinutes * 60_000;
  let lastNote = 0;
  while (Date.now() < deadline) {
    const entries = await fs.readdir(dir).catch(() => []);
    const downloading = entries.some(
      (e) => e.endsWith(".crdownload") || e.endsWith(".tmp"),
    );
    if (!downloading) {
      const zips = [];
      for (const e of entries) {
        if (!e.toLowerCase().endsWith(".zip")) continue;
        const st = await fs.stat(path.join(dir, e)).catch(() => null);
        if (st && st.size > 0 && st.mtimeMs >= sinceMs - 1000) {
          zips.push({ name: e, m: st.mtimeMs });
        }
      }
      if (zips.length) {
        zips.sort((a, b) => b.m - a.m);
        return path.join(dir, zips[0].name);
      }
    }
    if (Date.now() - lastNote > 30_000) {
      const left = Math.ceil((deadline - Date.now()) / 60_000);
      log(
        downloading
          ? "  …download in progress"
          : `  …waiting for the export to finish (≈${left} min left)`,
      );
      lastNote = Date.now();
    }
    await sleep(5_000);
  }
  return null;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const browserURL = args["browser-url"] || "http://127.0.0.1:9222";
  const downloadDir = path.resolve(
    args["download-dir"] || "./takeout-downloads",
  );
  const waitMinutes = Number(args["wait-minutes"] ?? 20);
  const configureOnly = Boolean(args["configure-only"]) || waitMinutes <= 0;

  await fs.mkdir(downloadDir, { recursive: true });

  log(`Connecting to Chrome at ${browserURL} …`);
  const browser = await puppeteer.connect({
    browserURL,
    defaultViewport: null,
  });

  try {
    const page = await browser.newPage();

    // Route downloads from this tab into our watched directory.
    const client = await page.createCDPSession();
    try {
      await client.send("Browser.setDownloadBehavior", {
        behavior: "allow",
        downloadPath: downloadDir,
        eventsEnabled: true,
      });
    } catch {
      await client.send("Page.setDownloadBehavior", {
        behavior: "allow",
        downloadPath: downloadDir,
      });
    }

    log("Opening Google Takeout …");
    await page.goto("https://takeout.google.com/", {
      waitUntil: "domcontentloaded",
      timeout: 60_000,
    });
    await sleep(2_500);

    // --- Phase 1: best-effort configuration -------------------------------
    log("Pre-selecting a YouTube-only export …");
    const deselected = await clickByText(page, "Deselect all").catch(
      () => false,
    );
    log(
      deselected
        ? "  ✓ Deselected all products"
        : "  ! Could not find 'Deselect all' — do it manually in Chrome",
    );
    await sleep(800);

    const ytState = await ensureYouTubeSelected(page).catch(() => "error");
    log(
      ytState === "selected" || ytState === "already-selected"
        ? "  ✓ YouTube and YouTube Music selected"
        : `  ! Could not auto-select YouTube (${ytState}) — tick it manually`,
    );

    // The deeper choices (limit to history + playlists; set history format to
    // JSON to preserve watched-at timestamps) live in nested Polymer dialogs
    // that are too brittle to click blind. Guide the human instead.
    log("");
    log("In the Chrome window, finish configuring the export:");
    log("  1. (optional) 'All YouTube data included' → keep only 'history'");
    log("     and 'playlists' to keep the export small.");
    log("  2. (recommended) 'Multiple formats' → set History to JSON so");
    log("     watched-at timestamps survive (default HTML drops them).");
    log("  3. Click 'Next step', then 'Create export'.");
    log("");

    await prompt(
      "When you have clicked 'Create export' (or to abort, type 'q'), press Enter… ",
    ).then((ans) => {
      if (ans.toLowerCase() === "q") {
        emit({ status: "aborted" });
        process.exit(0);
      }
    });

    if (configureOnly) {
      log(
        "Configure-only mode: skipping download wait. Google will email a link;",
      );
      log("run `yanger refresh --download` (or re-run) once it's ready.");
      emit({ status: "configured" });
      return;
    }

    // --- Phase 2: wait for the export to materialize ----------------------
    const since = Date.now();
    log(`Watching ${downloadDir} for the export zip …`);
    log(
      "Tip: when Google emails the link, click it in THIS Chrome window — the",
    );
    log("download will land in the watched folder and import automatically.");

    const zipPath = await waitForZip(downloadDir, since, waitMinutes);
    if (zipPath) {
      log(`✓ Got export: ${zipPath}`);
      emit({ status: "downloaded", zipPath });
    } else {
      log("Timed out waiting for the export download.");
      emit({ status: "timeout" });
    }
  } finally {
    // Detach, never close — it's the user's browser.
    browser.disconnect();
  }
}

main().catch((err) => {
  log(`Error: ${err && err.stack ? err.stack : err}`);
  emit({ status: "error", message: String((err && err.message) || err) });
  process.exit(1);
});
