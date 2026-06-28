# Takeout sync routine

The browser worker behind the `yanger sync` command. A Puppeteer routine that
makes it painless to grab a **fresh** Google Takeout
export of your YouTube history + playlists, then feed it into yanger.

It is **human-in-the-loop by design**:

- It attaches to a Chrome you are *already signed into* (via the remote-debugging
  port) — it **never types your password** or automates login. This keeps the
  flow inside Google's sanctioned data-portability path (Takeout) and away from
  login bot-detection / account flags.
- It pre-selects a YouTube-only export, then **you** review and click
  *Create export*. Takeout generates the file asynchronously; the routine then
  watches your download folder and hands the zip to yanger automatically.

## Normal use

You almost never call this directly — use the Python wrapper, which launches
Chrome with the right flags, runs this routine, and imports the result:

```bash
yanger sync
```

`yanger takeout` with **no file argument** also drops you into this flow
(it asks first, then runs `yanger sync`), so users never hit a dead end.

If the export isn't ready before the wait elapses, that's expected for large
accounts — the routine exits and the wrapper tells you to finish from the
"Your Google data is ready" email: download the zip and run
`yanger takeout ~/Downloads/takeout-XXXX.zip`.

## Running the routine directly

```bash
# 1. Start Chrome with a debug port on a DEDICATED profile.
#    (Chrome 136+ refuses --remote-debugging-port on the *default* profile, so
#    we use an isolated one. Log into Google in this window the first time.)
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.yanger/chrome-profile" \
  https://takeout.google.com/

# 2. Install deps (once) and run.
cd scripts/takeout-refresh
npm install
node refresh.js \
  --browser-url http://127.0.0.1:9222 \
  --download-dir "$HOME/.yanger/takeout-downloads" \
  --wait-minutes 20
```

### Flags

| Flag               | Default              | Meaning                                                        |
| ------------------ | -------------------- | -------------------------------------------------------------- |
| `--browser-url`    | `http://127.0.0.1:9222` | DevTools endpoint of your running Chrome.                   |
| `--download-dir`   | `./takeout-downloads`   | Folder watched for the finished `.zip`.                     |
| `--wait-minutes`   | `20`                 | How long to watch for the download (`0` = configure only).     |
| `--configure-only` | off                  | Submit the export and exit; download/import later.             |

### Output

Human progress and prompts go to **stderr**. A single JSON line is printed to
**stdout** at the end so the Python wrapper can act on it:

```json
{"status":"downloaded","zipPath":"/Users/you/.yanger/takeout-downloads/takeout-….zip"}
```

Other statuses: `configured`, `timeout`, `aborted`, `error`.

## Notes & limits

- **Asynchronous exports.** Big accounts can take minutes to hours. With
  `--configure-only` you can submit now and import later from the emailed link
  (click it in the same Chrome window so it lands in `--download-dir`).
- **Brittle DOM.** Takeout is a Polymer app with obfuscated class names. The
  "Deselect all" / "select YouTube" steps are best-effort and text-anchored; if
  Google changes the page they may no-op, in which case you finish those clicks
  by hand — the routine still waits for your *Create export* confirmation and
  handles the download.
- **Want zero scripting?** Takeout also offers native **scheduled exports**
  (every 2 months for a year, delivered to Drive). That's the lowest-maintenance
  way to keep your data current if you don't need on-demand sync.
