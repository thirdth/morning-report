# Morning Report

A local app that scans `~/Downloads`, scores every file by how deletable it looks, and surfaces the top candidates one at a time each morning. A scoring algorithm handles the structural signals (duplicates, junk filenames, age); Claude adds a one- or two-sentence read on the ones that need judgment. You click `[ trash ]`, `[ keep ]`, or `[ later ]` — nothing gets deleted without you.

## This is an example, not an app to download

This isn't packaged for distribution — it's the actual script running on my machine, published so the code is readable. A few things make that concrete:

- **macOS only.** File deletion goes through the Trash via `osascript`/AppleScript, the daily trigger is a `launchd` plist, and the installer builds a native `.app` bundle with `iconutil`. None of that exists on Linux or Windows.
- **The launchd plist needs a real absolute path**, because `launchd` daemons don't expand `$HOME`. `com.morningreport.plist` ships with a `YOUR_USERNAME` placeholder — swap it for your own before loading the job.
- **No installer wizard, no packaging, no auto-update.** Config lives in a JSON file you edit by hand or through the app's own settings panel.

If any of this is useful, treat it as a reference to fork and rewrite for your own machine, not a `git clone && run`.

## How it works

Every file in `~/Downloads` gets scored 0–100 before Claude ever sees it:

| signal | points |
|---|---|
| exact duplicate (SHA-256 match) | +50 |
| junk filename pattern (`.dmg`, `Screenshot`, `Untitled`, `(1)`, `copy`) | +20 |
| 180+ days old | +20 |
| 90–180 days old | +10 |
| tiny file < 100 KB | +5 |

The top N (10 by default, adjustable) go into a local web UI at `localhost:5757`. For each one, the app sends only filename, size, age, and type — never file contents — to the Anthropic API, and Claude returns a short recommendation. `launchd` opens the page automatically at 8am; `install_app.sh` also builds a `.app` you can launch by hand from `/Applications`.

Theme follows your OS light/dark setting by default, with a manual override (`auto` / `light` / `dark`) in the settings panel.

## Requirements

- macOS
- Python 3, standard library only — no `pip install` needed
- An [Anthropic API key](https://console.anthropic.com/)

## Setup

1. Clone this repo somewhere on your machine.
2. Run `install_app.sh`. It builds `Morning Report.app` and installs it to `/Applications` — launch it by hand from there whenever you want to run a session.
3. Launch the app once, open `[ config ]`, and set your Anthropic API key and Downloads folder path.
4. For the automatic 8am trigger: edit `com.morningreport.plist`, replacing every `YOUR_USERNAME` with your own, then `cp` it to `~/Library/LaunchAgents/` and `launchctl load ~/Library/LaunchAgents/com.morningreport.plist`. This step is optional — skip it if you're fine opening the app manually.

Config is stored in `~/.morning_report_config.json` (not tracked in this repo).

## Files

```
morning_report.py       — scanner, local HTTP server, Claude integration
install_app.sh           — builds the .app bundle + icon, installs to /Applications
com.morningreport.plist  — launchd job (8am daily trigger)
```
