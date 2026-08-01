#!/usr/bin/env python3
"""
Morning Report — Downloads Cleanup Game
Scans ~/Downloads, scores files for deletability, opens a local web UI
powered by the Anthropic API for per-file recommendations.
"""

import os
import sys
import json
import hashlib
import mimetypes
import subprocess
import threading
import time
import re
from datetime import datetime, timezone
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ── Config ────────────────────────────────────────────────────────────────────
CONFIG_FILE = Path.home() / ".morning_report_config.json"

DEFAULT_CONFIG = {
    "downloads_dir": str(Path.home() / "Downloads"),
    "files_per_session": 10,
    "min_age_days": 1,          # skip files touched in last N days
    "anthropic_api_key": "",    # set once; stored in config file
    "theme": "auto",            # auto | light | dark
}

def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        # backfill any missing keys from defaults
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        return cfg
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

# ── File scanning ─────────────────────────────────────────────────────────────
JUNK_PATTERNS = [
    r"\.dmg$", r"\.pkg$", r"\.zip$",              # installers / archives
    r"Screen Shot", r"Screenshot",                  # screenshots
    r"Untitled",                                     # untitled exports
    r" \(\d+\)\.",                                   # "file (1).pdf"
    r" copy\b",                                      # "file copy.pdf"
    r"-copy\b", r"_copy\b",                          # other copy variants
    r"\.crdownload$", r"\.part$",                    # incomplete downloads
]

def file_hash(path, chunk=65536):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while chunk_data := f.read(chunk):
                h.update(chunk_data)
        return h.hexdigest()
    except (PermissionError, OSError):
        return None

def score_file(f, hash_map, config):
    """Return a deletability score 0–100 and list of reasons."""
    score = 0
    reasons = []
    name = f["filename"]
    age = f["age_days"]

    if f["is_duplicate"]:
        score += 50
        reasons.append(f"Exact duplicate of {f['duplicate_of']}")

    for pat in JUNK_PATTERNS:
        if re.search(pat, name, re.IGNORECASE):
            score += 20
            label = pat.replace(r"\.", "").replace("$", "").replace(r"\b", "").replace(r"\d+", "N")
            reasons.append(f"Matches junk pattern: {label.strip()}")
            break

    if age > 180:
        score += 20
        reasons.append(f"{age} days old — never revisited")
    elif age > 90:
        score += 10
        reasons.append(f"{age} days old")

    if f["size_mb"] < 0.1:
        score += 5
        reasons.append("Tiny file (< 100 KB)")

    return min(score, 100), reasons

def scan_downloads(config):
    downloads = Path(config["downloads_dir"])
    min_age = config["min_age_days"]
    limit = config["files_per_session"]
    now = datetime.now(timezone.utc).timestamp()

    files = []
    for p in downloads.iterdir():
        if p.is_dir() or p.name.startswith("."):
            continue
        try:
            stat = p.stat()
        except OSError:
            continue
        age_days = (now - stat.st_mtime) / 86400
        if age_days < min_age:
            continue
        mime, _ = mimetypes.guess_type(str(p))
        files.append({
            "filename": p.name,
            "path": str(p),
            "size_mb": round(stat.st_size / 1_048_576, 2),
            "age_days": int(age_days),
            "last_modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d"),
            "mime_type": mime or "unknown",
            "is_duplicate": False,
            "duplicate_of": None,
        })

    # detect exact duplicates
    hash_map = {}
    for f in files:
        h = file_hash(f["path"])
        if h is None:
            continue
        if h in hash_map:
            f["is_duplicate"] = True
            f["duplicate_of"] = hash_map[h]
        else:
            hash_map[h] = f["filename"]

    # score and sort
    for f in files:
        f["score"], f["reasons"] = score_file(f, hash_map, config)

    files.sort(key=lambda x: x["score"], reverse=True)
    return files[:limit]

# ── HTTP server ───────────────────────────────────────────────────────────────
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="light dark">
<title>morning report</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
  /* tokens ported from zackglaser.com's src/styles/theme.css, kept in sync by hand */
  :root {
    --bg: #f2f4f6;
    --surface: #fafbfc;
    --surface2: #fafbfc;
    --border: #d6dbe0;
    --text: #1f2429;
    --muted: #616b75;
    --dim: #616b75;
    --danger: #c2295a;
    --safe: #3f7d5c;
    --later: #616b75;
    --font: 'Space Mono', 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #161a1e;
      --surface: #1f242a;
      --surface2: #1f242a;
      --border: #323a42;
      --text: #edf0f2;
      --muted: #98a2ac;
      --dim: #98a2ac;
      --danger: #e8698f;
      --safe: #6fbb94;
      --later: #98a2ac;
    }
  }
  /* explicit [data-theme] beats the media query in both directions —
     an attribute selector on :root outranks a bare :root, regardless of source order. */
  :root[data-theme="light"] {
    --bg: #f2f4f6;
    --surface: #fafbfc;
    --surface2: #fafbfc;
    --border: #d6dbe0;
    --text: #1f2429;
    --muted: #616b75;
    --dim: #616b75;
    --danger: #c2295a;
    --safe: #3f7d5c;
    --later: #616b75;
  }
  :root[data-theme="dark"] {
    --bg: #161a1e;
    --surface: #1f242a;
    --surface2: #1f242a;
    --border: #323a42;
    --text: #edf0f2;
    --muted: #98a2ac;
    --dim: #98a2ac;
    --danger: #e8698f;
    --safe: #6fbb94;
    --later: #98a2ac;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--font);
    font-size: 13px;
    line-height: 1.6;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 56px 24px 80px;
    transition: background 0.25s ease, color 0.25s ease;
  }
  header {
    width: 100%;
    max-width: 520px;
    margin-bottom: 48px;
    border-bottom: 1px solid var(--border);
    padding-bottom: 24px;
  }
  .site-label {
    font-size: 11px;
    color: var(--muted);
    margin-bottom: 16px;
    letter-spacing: 0.05em;
  }
  h1 {
    font-size: 15px;
    font-weight: 400;
    color: var(--text);
    margin-bottom: 4px;
  }
  .subtitle {
    font-size: 12px;
    color: var(--muted);
  }
  .progress-wrap {
    width: 100%;
    max-width: 520px;
    display: flex;
    align-items: center;
    gap: 16px;
    margin-bottom: 32px;
  }
  .progress-bar {
    flex: 1;
    height: 1px;
    background: var(--border);
    overflow: hidden;
  }
  .progress-fill {
    height: 100%;
    background: var(--dim);
    transition: width 0.4s ease;
  }
  .progress-label {
    font-size: 11px;
    color: var(--muted);
    white-space: nowrap;
    flex-shrink: 0;
  }
  .card {
    width: 100%;
    max-width: 520px;
    border-top: 1px solid var(--border);
    padding: 28px 0;
    margin-bottom: 0;
  }
  .file-header {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 20px;
  }
  .filename {
    font-size: 13px;
    color: var(--text);
    word-break: break-all;
    line-height: 1.5;
  }
  .score-badge {
    flex-shrink: 0;
    font-size: 11px;
    color: var(--muted);
    white-space: nowrap;
  }
  .score-high .score-badge { color: var(--danger); }
  .score-mid  .score-badge { color: var(--text); }
  .score-low  .score-badge { color: var(--muted); }
  .meta {
    display: flex;
    gap: 24px;
    margin-bottom: 16px;
    flex-wrap: wrap;
  }
  .meta-item {
    display: flex;
    gap: 6px;
    align-items: baseline;
  }
  .meta-label {
    font-size: 11px;
    color: var(--muted);
  }
  .meta-value {
    font-size: 12px;
    color: var(--text);
  }
  .reasons {
    margin-bottom: 20px;
    color: var(--muted);
    font-size: 11px;
    line-height: 1.8;
  }
  .reason-tag {
    display: inline;
  }
  .reason-tag::before { content: "— "; }
  .ai-block {
    border-left: 1px solid var(--border);
    padding-left: 16px;
    margin-bottom: 24px;
  }
  .ai-label {
    font-size: 11px;
    color: var(--muted);
    margin-bottom: 6px;
  }
  .ai-recommendation {
    font-size: 13px;
    line-height: 1.7;
    color: var(--text);
    min-height: 44px;
  }
  .ai-recommendation.loading {
    color: var(--muted);
  }
  .actions {
    display: flex;
    gap: 0;
  }
  button {
    flex: none;
    padding: 0;
    border: none;
    background: none;
    font-family: var(--font);
    font-size: 13px;
    cursor: pointer;
    transition: color 0.1s;
    color: var(--muted);
    margin-right: 24px;
  }
  button:hover { color: var(--text); }
  .btn-trash:hover { color: var(--danger); }
  .btn-keep:hover  { color: var(--safe); }
  .btn-later:hover { color: var(--text); }
  .summary-card {
    width: 100%;
    max-width: 520px;
    border-top: 1px solid var(--border);
    padding-top: 32px;
    display: none;
  }
  .summary-card h2 {
    font-size: 13px;
    font-weight: 400;
    margin-bottom: 4px;
  }
  .summary-card p {
    color: var(--muted);
    font-size: 12px;
    margin-bottom: 32px;
  }
  .stat-row {
    display: flex;
    gap: 40px;
    margin: 0 0 32px;
  }
  .stat { }
  .stat-num {
    font-size: 24px;
    color: var(--text);
    display: block;
    margin-bottom: 2px;
  }
  .stat-lbl {
    font-size: 11px;
    color: var(--muted);
  }
  .settings-link {
    font-family: var(--font);
    font-size: 11px;
    color: var(--muted);
    cursor: pointer;
    background: none;
    border: none;
    padding: 0;
    flex: none;
    width: auto;
    margin-right: 0;
  }
  .settings-link:hover { color: var(--text); }
  .settings-panel {
    width: 100%;
    max-width: 520px;
    border-top: 1px solid var(--border);
    padding: 24px 0;
    margin-bottom: 24px;
    display: none;
  }
  .settings-panel h3 {
    font-size: 11px;
    font-weight: 400;
    margin-bottom: 20px;
    color: var(--muted);
    text-transform: lowercase;
  }
  .setting-row {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    padding: 10px 0;
    border-bottom: 1px solid var(--border);
    gap: 16px;
  }
  .setting-row:last-child { border-bottom: none; }
  .setting-label { font-size: 12px; color: var(--text); }
  .setting-desc { font-size: 11px; color: var(--muted); margin-top: 2px; }
  input[type=number], input[type=text], input[type=password], select {
    background: none;
    border: none;
    border-bottom: 1px solid var(--border);
    color: var(--text);
    font-family: var(--font);
    font-size: 12px;
    padding: 2px 0;
    width: 64px;
    text-align: right;
    outline: none;
  }
  input[type=number]:focus, input[type=text]:focus, input[type=password]:focus, select:focus {
    border-bottom-color: var(--dim);
  }
  input[type=text], input[type=password] {
    width: 200px;
    text-align: left;
  }
  select {
    width: 90px;
    -webkit-appearance: none;
    appearance: none;
    border-radius: 0;
    cursor: pointer;
  }
  select option {
    background: var(--bg);
    color: var(--text);
  }
  .save-btn {
    margin-top: 20px;
    background: none;
    border: none;
    border-bottom: 1px solid var(--border);
    color: var(--muted);
    font-family: var(--font);
    font-size: 12px;
    cursor: pointer;
    padding: 0 0 2px;
    flex: none;
    width: auto;
    margin-right: 0;
  }
  .save-btn:hover { color: var(--text); border-bottom-color: var(--dim); }
  .toast {
    position: fixed;
    bottom: 24px;
    left: 50%;
    transform: translateX(-50%);
    background: var(--surface2);
    border: 1px solid var(--border);
    padding: 8px 16px;
    font-size: 12px;
    color: var(--muted);
    opacity: 0;
    transition: opacity 0.25s;
    pointer-events: none;
    white-space: nowrap;
  }
  .toast.show { opacity: 1; }
</style>
</head>
<body>

<header>
  <div class="site-label">zackglaser / morning-report</div>
  <h1>morning report.</h1>
  <div class="subtitle"><span id="fileCount">—</span> candidates in downloads — <span id="dateStr"></span></div>
</header>

<div class="progress-wrap" style="max-width:520px;width:100%">
  <div class="progress-bar">
    <div class="progress-fill" id="progressFill" style="width:0%"></div>
  </div>
  <div class="progress-label" id="progressLabel"></div>
  <button class="settings-link" onclick="toggleSettings()">[ config ]</button>
</div>

<div class="settings-panel" id="settingsPanel">
  <h3>config</h3>
  <div class="setting-row">
    <div>
      <div class="setting-label">theme</div>
      <div class="setting-desc">auto follows your system setting</div>
    </div>
    <select id="cfg_theme" onchange="previewTheme()">
      <option value="auto">auto</option>
      <option value="light">light</option>
      <option value="dark">dark</option>
    </select>
  </div>
  <div class="setting-row">
    <div>
      <div class="setting-label">files per session</div>
      <div class="setting-desc">candidates reviewed each morning</div>
    </div>
    <input type="number" id="cfg_files_per_session" min="1" max="50" value="10">
  </div>
  <div class="setting-row">
    <div>
      <div class="setting-label">minimum file age (days)</div>
      <div class="setting-desc">skip files newer than this</div>
    </div>
    <input type="number" id="cfg_min_age_days" min="0" max="365" value="1">
  </div>
  <div class="setting-row">
    <div>
      <div class="setting-label">downloads folder</div>
      <div class="setting-desc">full path to scan</div>
    </div>
    <input type="text" id="cfg_downloads_dir" value="">
  </div>
  <div class="setting-row">
    <div>
      <div class="setting-label">anthropic api key</div>
      <div class="setting-desc">stored in ~/.morning_report_config.json</div>
    </div>
    <input type="password" id="cfg_anthropic_api_key" placeholder="sk-ant-...">
  </div>
  <button class="save-btn" onclick="saveSettings()">[ save &amp; restart ]</button>
</div>

<div id="cardContainer"></div>

<div class="summary-card" id="summaryCard">
  <h2>done for today.</h2>
  <p>that's your <span id="sessionLimit">10</span>. come back tomorrow.</p>
  <div class="stat-row">
    <div class="stat">
      <span class="stat-num" id="statTrashed">0</span>
      <div class="stat-lbl">trashed</div>
    </div>
    <div class="stat">
      <span class="stat-num" id="statFreed">0</span>
      <div class="stat-lbl">mb freed</div>
    </div>
    <div class="stat">
      <span class="stat-num" id="statKept">0</span>
      <div class="stat-lbl">kept</div>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
let files = [];
let currentIndex = 0;
let stats = { trashed: 0, kept: 0, later: 0, mbFreed: 0 };
let config = {};

// ── Init ──────────────────────────────────────────────────────────────────────
document.getElementById("dateStr").textContent =
  new Date().toLocaleDateString("en-US", { weekday:"long", month:"long", day:"numeric" }).toLowerCase();

async function init() {
  const [cfgRes, filesRes] = await Promise.all([
    fetch("/api/config"),
    fetch("/api/files")
  ]);
  config = await cfgRes.json();
  files = await filesRes.json();

  // populate settings panel
  document.getElementById("cfg_theme").value = config.theme || "auto";
  document.getElementById("cfg_files_per_session").value = config.files_per_session;
  document.getElementById("cfg_min_age_days").value = config.min_age_days;
  document.getElementById("cfg_downloads_dir").value = config.downloads_dir;
  document.getElementById("cfg_anthropic_api_key").value = config.anthropic_api_key || "";
  document.getElementById("sessionLimit").textContent = config.files_per_session;

  document.getElementById("fileCount").textContent = files.length;
  updateProgress();
  showNext();
}

// ── Progress ──────────────────────────────────────────────────────────────────
function updateProgress() {
  const pct = files.length ? (currentIndex / files.length) * 100 : 0;
  document.getElementById("progressFill").style.width = pct + "%";
  document.getElementById("progressLabel").textContent =
    files.length ? `${currentIndex} of ${files.length} reviewed` : "";
}

// ── Card rendering ────────────────────────────────────────────────────────────
function scoreClass(s) {
  if (s >= 60) return "score-high";
  if (s >= 30) return "score-mid";
  return "score-low";
}

function showNext() {
  const container = document.getElementById("cardContainer");
  container.innerHTML = "";

  if (currentIndex >= files.length) {
    document.getElementById("summaryCard").style.display = "block";
    document.getElementById("statTrashed").textContent = stats.trashed;
    document.getElementById("statFreed").textContent = stats.mbFreed.toFixed(1);
    document.getElementById("statKept").textContent = stats.kept;
    return;
  }

  const f = files[currentIndex];
  const sc = scoreClass(f.score);

  const card = document.createElement("div");
  card.className = `card ${sc}`;
  card.innerHTML = `
    <div class="file-header">
      <div class="filename">${escHtml(f.filename)}</div>
      <div class="score-badge">${f.score}/100</div>
    </div>
    <div class="meta">
      <div class="meta-item">
        <span class="meta-label">size</span>
        <span class="meta-value">${f.size_mb} mb</span>
      </div>
      <div class="meta-item">
        <span class="meta-label">age</span>
        <span class="meta-value">${f.age_days}d</span>
      </div>
      <div class="meta-item">
        <span class="meta-label">modified</span>
        <span class="meta-value">${f.last_modified}</span>
      </div>
      <div class="meta-item">
        <span class="meta-label">type</span>
        <span class="meta-value">${f.mime_type.split("/")[1] || "unknown"}</span>
      </div>
    </div>
    <div class="reasons">
      ${f.reasons.map(r => `<span class="reason-tag">${escHtml(r)}</span>`).join("")}
    </div>
    <div class="ai-block">
      <div class="ai-label">claude says</div>
      <div class="ai-recommendation loading" id="aiRec">analyzing…</div>
    </div>
    <div class="actions">
      <button class="btn-trash" onclick="decide('trash')">[ trash ]</button>
      <button class="btn-keep"  onclick="decide('keep')">[ keep ]</button>
      <button class="btn-later" onclick="decide('later')">[ later ]</button>
    </div>
  `;
  container.appendChild(card);
  fetchRecommendation(f);
}

async function fetchRecommendation(f) {
  const el = document.getElementById("aiRec");
  try {
    const res = await fetch("/api/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(f)
    });
    const data = await res.json();
    el.textContent = data.recommendation;
    el.classList.remove("loading");
  } catch (e) {
    el.textContent = "could not reach claude. check your api key in [ config ].";
    el.classList.remove("loading");
  }
}

async function decide(action) {
  const f = files[currentIndex];
  if (action === "trash") {
    stats.trashed++;
    stats.mbFreed += f.size_mb;
    await fetch("/api/trash", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: f.path })
    });
    showToast(`Trashed ${f.filename}`);
  } else if (action === "keep") {
    stats.kept++;
    showToast("Kept ✓");
  } else {
    showToast("Skipped for later");
  }
  currentIndex++;
  updateProgress();
  showNext();
}

// ── Settings ──────────────────────────────────────────────────────────────────
function toggleSettings() {
  const p = document.getElementById("settingsPanel");
  const isHidden = getComputedStyle(p).display === "none";
  p.style.display = isHidden ? "block" : "none";
}

function previewTheme() {
  const theme = document.getElementById("cfg_theme").value;
  if (theme === "light" || theme === "dark") {
    document.documentElement.setAttribute("data-theme", theme);
  } else {
    document.documentElement.removeAttribute("data-theme");
  }
}

async function saveSettings() {
  const updated = {
    theme: document.getElementById("cfg_theme").value,
    files_per_session: parseInt(document.getElementById("cfg_files_per_session").value),
    min_age_days: parseInt(document.getElementById("cfg_min_age_days").value),
    downloads_dir: document.getElementById("cfg_downloads_dir").value.trim(),
    anthropic_api_key: document.getElementById("cfg_anthropic_api_key").value.trim(),
  };
  await fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updated)
  });
  showToast("Saved — reloading…");
  setTimeout(() => location.reload(), 800);
}

// ── Utils ─────────────────────────────────────────────────────────────────────
function escHtml(s) {
  return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

function showToast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 2000);
}

init();
</script>
</body>
</html>
"""

class Handler(BaseHTTPRequestHandler):
    files_cache = None
    config_cache = None

    def log_message(self, *args): pass  # silence access logs

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length))

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/" or path == "/index.html":
            theme = Handler.config_cache.get("theme", "auto")
            html = HTML_TEMPLATE
            if theme in ("light", "dark"):
                html = html.replace('<html lang="en">', f'<html lang="en" data-theme="{theme}">', 1)
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/api/config":
            self.send_json(Handler.config_cache)

        elif path == "/api/files":
            if Handler.files_cache is None:
                Handler.files_cache = scan_downloads(Handler.config_cache)
            self.send_json(Handler.files_cache)

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/api/recommend":
            f = self.read_json_body()
            rec = get_claude_recommendation(f, Handler.config_cache)
            self.send_json({"recommendation": rec})

        elif path == "/api/trash":
            data = self.read_json_body()
            trash_file(data["path"])
            self.send_json({"ok": True})

        elif path == "/api/config":
            updates = self.read_json_body()
            Handler.config_cache.update(updates)
            save_config(Handler.config_cache)
            Handler.files_cache = None  # bust cache
            self.send_json({"ok": True})

        else:
            self.send_response(404)
            self.end_headers()


# ── Claude integration ────────────────────────────────────────────────────────
def get_claude_recommendation(f, config):
    import urllib.request
    api_key = config.get("anthropic_api_key", "")
    if not api_key:
        return "No API key set. Add your Anthropic API key in Settings to enable Claude recommendations."

    prompt = f"""You are a ruthless but fair file cleanup assistant. A user is reviewing their Downloads folder each morning and needs a quick, direct recommendation for each file.

File details:
- Filename: {f['filename']}
- Size: {f['size_mb']} MB
- Age: {f['age_days']} days old (last modified {f['last_modified']})
- Type: {f['mime_type']}
- Is exact duplicate: {f['is_duplicate']}
{f'- Duplicate of: {f["duplicate_of"]}' if f['is_duplicate'] else ''}
- Flagged reasons: {', '.join(f['reasons']) if f['reasons'] else 'none'}

Give a 1-2 sentence recommendation. End with a clear verdict: **Trash it.**, **Keep it.**, or **Your call.**
Be direct. No hedging unless genuinely ambiguous."""

    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 150,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return data["content"][0]["text"]
    except Exception as e:
        return f"Claude unavailable: {str(e)}"


# ── Trash ─────────────────────────────────────────────────────────────────────
def trash_file(path):
    """Move to macOS Trash via AppleScript — fully recoverable."""
    script = f'tell application "Finder" to delete POSIX file "{path}"'
    subprocess.run(["osascript", "-e", script], capture_output=True)


# ── Entry point ───────────────────────────────────────────────────────────────
def run():
    config = load_config()
    save_config(config)  # ensure file exists

    Handler.config_cache = config
    Handler.files_cache = None

    port = 5757
    server = HTTPServer(("127.0.0.1", port), Handler)
    url = f"http://localhost:{port}"

    # open browser after a brief delay
    def open_browser():
        time.sleep(0.5)
        subprocess.run(["open", url])

    threading.Thread(target=open_browser, daemon=True).start()
    print(f"Morning Report running at {url}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nGoodbye.")

if __name__ == "__main__":
    run()
