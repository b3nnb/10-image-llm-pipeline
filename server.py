#!/usr/bin/env python3
"""
Friday Image Pipeline — Web UI Server

Serves a browser-based UI for generating, browsing, iterating, and approving images.
The gallery auto-refreshes, and you can trigger generations directly from the browser.

Usage:
  python server.py           # starts on http://localhost:8765
  python server.py --port 9000
"""

import argparse
import base64
import json
import os
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Re-use the pipeline logic
sys.path.insert(0, str(Path(__file__).parent))
from pipeline import (
    get_base_prompt, get_negative_prompt, load_persona_config, save_persona_config,
    OUTPUT_DIR, STATE_FILE, AVAILABLE_MODELS, DEFAULT_MODEL,
    
    load_state, save_state, build_workflow, build_faceid_workflow, get_reference_image_b64,
    queue_prompt, wait_for_completion, fetch_image,
    COMFY_URL,
)
from scenes import SCENE_PACKS

# Background job queue
_job_queue: list[dict] = []
_job_lock = threading.Lock()
_active_job: dict | None = None


def run_job(job: dict):
    """Execute a generation job in the background."""
    global _active_job
    run_id = job["run_id"]
    state = load_state()

    state["runs"][run_id]["status"] = "generating"
    save_state(state)

    try:
        positive = get_base_prompt() + ', ' + job.get('scene', '')
        model_file = AVAILABLE_MODELS.get(job.get("model", DEFAULT_MODEL), AVAILABLE_MODELS[DEFAULT_MODEL])
        seed = job.get("seed", -1)
        use_faceid = job.get("faceid", False)

        if use_faceid:
            ref_b64 = get_reference_image_b64()
            if ref_b64:
                workflow = build_faceid_workflow(positive, model_file, ref_b64, seed=seed)
                actual_seed = workflow["8"]["inputs"]["seed"]
            else:
                # Fall back to standard if no reference set
                workflow = build_workflow(positive, model_file, seed=seed)
                actual_seed = workflow["3"]["inputs"]["seed"]
                state = load_state()
                if run_id in state["runs"]:
                    state["runs"][run_id]["faceid"] = False
                    save_state(state)
        else:
            workflow = build_workflow(positive, model_file, seed=seed)
            actual_seed = workflow["3"]["inputs"]["seed"]

        prompt_id = queue_prompt(workflow)
        history = wait_for_completion(prompt_id)
        run_dir = OUTPUT_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        images_saved = []
        outputs = history.get("outputs", {})
        for node_id, node_output in outputs.items():
            for img in node_output.get("images", []):
                dest = run_dir / img["filename"]
                fetch_image(img["filename"], img.get("subfolder", ""), dest)
                images_saved.append(str(dest))

        state = load_state()
        state["runs"][run_id]["status"] = "done"
        state["runs"][run_id]["images"] = images_saved
        state["runs"][run_id]["seed"] = actual_seed
        save_state(state)

    except Exception as e:
        state = load_state()
        if run_id in state["runs"]:
            state["runs"][run_id]["status"] = "error"
            state["runs"][run_id]["error"] = str(e)
            save_state(state)

    _active_job = None


def worker_thread():
    """Background thread that processes jobs one at a time."""
    global _active_job
    while True:
        with _job_lock:
            if _job_queue and _active_job is None:
                job = _job_queue.pop(0)
                _active_job = job
            else:
                job = None

        if job:
            run_job(job)
        else:
            time.sleep(1)


def enqueue_generation(scene: str, model: str = DEFAULT_MODEL, seed: int = -1,
                        parent_id: "str | None" = None, version: int = 1,
                        use_faceid: bool = False) -> str:
    """Add a generation to the queue and return its run_id."""
    run_id = str(uuid.uuid4())[:8]
    state = load_state()
    state["runs"][run_id] = {
        "id": run_id,
        "scene": scene,
        "model": model,
        "seed": seed,
        "version": version,
        "parent_id": parent_id,
        "status": "queued",
        "approved": False,
        "faceid": use_faceid,
        "images": [],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    save_state(state)

    with _job_lock:
        _job_queue.append({"run_id": run_id, "scene": scene, "model": model, "seed": seed, "faceid": use_faceid})

    return run_id


def img_to_data_uri(path: str) -> str:
    try:
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        return f"data:image/png;base64,{data}"
    except Exception:
        return ""


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Friday Image Pipeline</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: #0f0f13; color: #e0e0e8; font-family: 'Segoe UI', system-ui, sans-serif; min-height: 100vh; }
    header { background: #17171f; border-bottom: 1px solid #2a2a3a; padding: 16px 24px; display: flex; align-items: center; gap: 16px; }
    header h1 { font-size: 1.2rem; font-weight: 600; color: #9b8ef0; }
    header .sub { font-size: 0.8rem; color: #6b6b8a; }
    .status-dot { width: 8px; height: 8px; border-radius: 50%; background: #3f3f5a; display: inline-block; margin-right: 6px; }
    .status-dot.active { background: #5cdb8a; animation: pulse 1.5s infinite; }
    @keyframes pulse { 0%,100%{opacity:1}50%{opacity:0.4} }

    .layout { display: grid; grid-template-columns: 340px 1fr; gap: 0; min-height: calc(100vh - 57px); }
    .sidebar { background: #13131a; border-right: 1px solid #2a2a3a; padding: 20px; overflow-y: auto; }
    .main { padding: 20px; overflow-y: auto; }

    .section-title { font-size: 0.7rem; font-weight: 600; color: #6b6b8a; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 12px; }

    .generate-form { background: #1a1a24; border: 1px solid #2a2a3a; border-radius: 8px; padding: 16px; margin-bottom: 20px; }
    .generate-form textarea { width: 100%; background: #0f0f16; border: 1px solid #2a2a3a; border-radius: 6px; color: #e0e0e8; padding: 10px; font-size: 0.85rem; resize: vertical; min-height: 80px; outline: none; }
    .generate-form textarea:focus { border-color: #5b4fe0; }
    .row { display: flex; gap: 8px; margin-top: 10px; align-items: center; }
    select { background: #0f0f16; border: 1px solid #2a2a3a; color: #e0e0e8; border-radius: 6px; padding: 7px 10px; font-size: 0.82rem; }
    .btn { padding: 8px 16px; border-radius: 6px; border: none; cursor: pointer; font-size: 0.82rem; font-weight: 600; transition: opacity 0.15s; }
    .btn:hover { opacity: 0.85; }
    .btn-primary { background: #5b4fe0; color: white; }
    .btn-sm { padding: 5px 10px; font-size: 0.75rem; }
    .btn-approve { background: #2e6b4a; color: #5cdb8a; }
    .btn-iterate { background: #2a3060; color: #7b9cf0; }
    .btn-danger { background: #4a1f1f; color: #e07b7b; }

    .presets { margin-bottom: 20px; }
    .preset-pack { margin-bottom: 12px; }
    .preset-pack-title { font-size: 0.78rem; font-weight: 600; color: #9b8ef0; margin-bottom: 6px; text-transform: capitalize; }
    .preset-scene { background: #1a1a24; border: 1px solid #2a2a3a; border-radius: 5px; padding: 8px 10px; margin-bottom: 4px; font-size: 0.75rem; color: #b0b0cc; cursor: pointer; transition: border-color 0.15s; line-height: 1.4; }
    .preset-scene:hover { border-color: #5b4fe0; color: #e0e0e8; }

    .queue-section { margin-bottom: 20px; }
    .queue-item { background: #1a1a24; border: 1px solid #2a2a3a; border-radius: 6px; padding: 10px 12px; margin-bottom: 6px; font-size: 0.78rem; }
    .queue-item .scene-text { color: #b0b0cc; margin-top: 3px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .badge { display: inline-block; padding: 2px 7px; border-radius: 10px; font-size: 0.7rem; font-weight: 600; }
    .badge-queued { background: #2a2a40; color: #7b7baa; }
    .badge-generating { background: #2a3060; color: #7b9cf0; }
    .badge-done { background: #1e3b2e; color: #5cdb8a; }
    .badge-error { background: #3b1e1e; color: #e07b7b; }
    .badge-approved { background: #2e4a1e; color: #9de05b; }

    .filters { display: flex; gap: 6px; margin-bottom: 16px; flex-wrap: wrap; }
    .filter-btn { padding: 5px 12px; border-radius: 20px; border: 1px solid #2a2a3a; background: transparent; color: #6b6b8a; font-size: 0.75rem; cursor: pointer; transition: all 0.15s; }
    .filter-btn.active, .filter-btn:hover { border-color: #5b4fe0; color: #9b8ef0; background: #1e1a35; }

    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; }
    .card { background: #1a1a24; border: 1px solid #2a2a3a; border-radius: 10px; overflow: hidden; transition: border-color 0.15s; }
    .card:hover { border-color: #3d3d5a; }
    .card.approved { border-color: #2e6b4a; }
    .card img { width: 100%; aspect-ratio: 1; object-fit: cover; display: block; background: #0f0f16; }
    .card-body { padding: 12px; }
    .card-meta { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; }
    .card-scene { font-size: 0.78rem; color: #b0b0cc; margin-bottom: 10px; line-height: 1.4; }
    .card-actions { display: flex; gap: 6px; flex-wrap: wrap; }
    .card-id { font-size: 0.7rem; color: #4a4a6a; font-family: monospace; }

    .iterate-modal { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.7); z-index: 100; align-items: center; justify-content: center; }
    .iterate-modal.open { display: flex; }
    .modal-box { background: #17171f; border: 1px solid #2a2a3a; border-radius: 10px; padding: 24px; width: 480px; max-width: 95vw; }
    .modal-box h3 { margin-bottom: 14px; color: #9b8ef0; }
    .modal-box textarea { width: 100%; background: #0f0f16; border: 1px solid #2a2a3a; border-radius: 6px; color: #e0e0e8; padding: 10px; font-size: 0.85rem; resize: vertical; min-height: 80px; outline: none; margin-bottom: 12px; }
    .modal-actions { display: flex; gap: 8px; justify-content: flex-end; }
    .btn-cancel { background: #2a2a3a; color: #8080a0; }

    #toast { position: fixed; bottom: 20px; right: 20px; background: #2a3060; color: #9b8ef0; border: 1px solid #4a4fe0; border-radius: 8px; padding: 12px 18px; font-size: 0.85rem; opacity: 0; transition: opacity 0.3s; z-index: 200; pointer-events: none; }
    #toast.show { opacity: 1; }

    .empty-state { text-align: center; padding: 60px 20px; color: #4a4a6a; }
    .empty-state .emoji { font-size: 3rem; margin-bottom: 12px; }
  </style>
</head>
<body>
<header>
  <div>
    <h1>🎨 Friday Image Pipeline</h1>
    <div class="sub"><span class="status-dot" id="worker-dot"></span><span id="worker-status">Idle</span></div>
  </div>
</header>
<div class="layout">
  <div class="sidebar">
    <div class="section-title">Generate</div>
    <div class="generate-form">
      <textarea id="scene-input" placeholder="Describe the scene... (Friday is automatically included)"></textarea>
      <div class="row">
        <select id="model-select">
          <option value="juggernaut">Juggernaut XL (photorealistic)</option>
          <option value="animagine">Animagine XL (anime/illustrated)</option>
        </select>
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;color:#ccc;cursor:pointer">
          <input type="checkbox" id="faceid-toggle" style="cursor:pointer">
          FaceID
        </label>
        <button class="btn btn-primary" onclick="submitGenerate()">Generate</button>
      </div>
    </div>

    <div class="section-title">Scene Presets</div>
    <div class="presets" id="presets-container"></div>

    <div class="section-title" style="margin-top:24px">⚙️ Persona Config</div>
    <div class="generate-form" id="persona-form">
      <label style="font-size:11px;color:#888;display:block;margin-bottom:4px">Base prompt (who Friday is)</label>
      <textarea id="persona-base" rows="4" placeholder="Loading..."></textarea>
      <label style="font-size:11px;color:#888;display:block;margin:8px 0 4px">Negative prompt</label>
      <textarea id="persona-negative" rows="3" placeholder="Loading..."></textarea>
      <button class="btn btn-primary" style="margin-top:8px;width:100%" onclick="savePersona()">Save Persona</button>
    </div>
  </div>

  <div class="main">
    <div class="filters" id="filters">
      <button class="filter-btn active" onclick="setFilter('all', this)">All</button>
      <button class="filter-btn" onclick="setFilter('approved', this)">Approved ✅</button>
      <button class="filter-btn" onclick="setFilter('pending', this)">Pending</button>
      <button class="filter-btn" onclick="setFilter('generating', this)">In Progress</button>
    </div>
    <div class="grid" id="gallery"></div>
    <div class="empty-state" id="empty-state" style="display:none">
      <div class="emoji">🖼️</div>
      <div>No images yet. Generate your first scene!</div>
    </div>
  </div>
</div>

<div class="iterate-modal" id="iterate-modal">
  <div class="modal-box">
    <h3>Iterate on this image</h3>
    <textarea id="iterate-feedback" placeholder="What should change? e.g. make the lighting warmer, different background, more professional look..."></textarea>
    <div class="modal-actions">
      <button class="btn btn-cancel" onclick="closeIterate()">Cancel</button>
      <button class="btn btn-primary" onclick="submitIterate()">Queue Iteration</button>
    </div>
  </div>
</div>

<div id="toast"></div>

<script>
const PRESETS = __PRESETS__;
let currentFilter = 'all';
let iterateTargetId = null;
let pollInterval = null;

// Render scene presets in sidebar
function renderPresets() {
  const container = document.getElementById('presets-container');
  for (const [pack, scenes] of Object.entries(PRESETS)) {
    const packDiv = document.createElement('div');
    packDiv.className = 'preset-pack';
    packDiv.innerHTML = `<div class="preset-pack-title">${pack}</div>`;
    for (const scene of scenes) {
      const sceneDiv = document.createElement('div');
      sceneDiv.className = 'preset-scene';
      sceneDiv.textContent = scene.length > 80 ? scene.slice(0, 77) + '...' : scene;
      sceneDiv.title = scene;
      sceneDiv.onclick = () => {
        document.getElementById('scene-input').value = scene;
        document.getElementById('scene-input').focus();
      };
      packDiv.appendChild(sceneDiv);
    }
    container.appendChild(packDiv);
  }
}

function setFilter(f, btn) {
  currentFilter = f;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderGallery(lastState);
}

function badgeHtml(run) {
  if (run.approved) return '<span class="badge badge-approved">✅ Approved</span>';
  if (run.status === 'queued') return '<span class="badge badge-queued">Queued</span>';
  if (run.status === 'generating') return '<span class="badge badge-generating">⚙️ Generating...</span>';
  if (run.status === 'error') return '<span class="badge badge-error">Error</span>';
  return '<span class="badge badge-done">Done</span>';
}

let lastState = null;

function renderGallery(state) {
  lastState = state;
  const gallery = document.getElementById('gallery');
  const empty = document.getElementById('empty-state');
  const runs = Object.values(state.runs || {}).reverse();

  const filtered = runs.filter(r => {
    if (currentFilter === 'approved') return r.approved;
    if (currentFilter === 'pending') return !r.approved && r.status === 'done';
    if (currentFilter === 'generating') return r.status === 'queued' || r.status === 'generating';
    return true;
  });

  if (filtered.length === 0) {
    gallery.innerHTML = '';
    empty.style.display = 'block';
    return;
  }
  empty.style.display = 'none';

  gallery.innerHTML = filtered.map(run => {
    const imgHtml = run.images && run.images.length > 0
      ? `<img src="/image/${run.id}/${encodeURIComponent(run.images[0].split('/').pop())}" loading="lazy" onerror="this.style.opacity=0.2">`
      : `<div style="width:100%;aspect-ratio:1;background:#0f0f16;display:flex;align-items:center;justify-content:center;font-size:2rem;">${run.status === 'generating' ? '⚙️' : run.status === 'queued' ? '⏳' : '📷'}</div>`;

    const scene = run.scene || '';
    const shortScene = scene.length > 100 ? scene.slice(0, 97) + '...' : scene;

    return `<div class="card ${run.approved ? 'approved' : ''}" id="card-${run.id}">
      ${imgHtml}
      <div class="card-body">
        <div class="card-meta">
          ${badgeHtml(run)}
          <span class="badge" style="background:#1a1a30;color:#5a5a80">v${run.version || 1}</span>
          <span class="card-id">#${run.id}</span>
        </div>
        <div class="card-scene" title="${scene}">${shortScene}</div>
        <div class="card-actions">
          ${run.status === 'done' && !run.approved
            ? `<button class="btn btn-sm btn-approve" onclick="approve('${run.id}')">✅ Approve</button>`
            : ''}
          ${run.status === 'done'
            ? `<button class="btn btn-sm btn-iterate" onclick="openIterate('${run.id}')">🔄 Iterate</button>`
            : ''}
          ${run.approved
            ? `<button class="btn btn-sm btn-danger" onclick="unapprove('${run.id}')">↩ Unapprove</button>`
            : ''}
        </div>
      </div>
    </div>`;
  }).join('');
}

function updateWorkerStatus(state) {
  const dot = document.getElementById('worker-dot');
  const label = document.getElementById('worker-status');
  const generating = Object.values(state.runs || {}).some(r => r.status === 'generating');
  const queued = Object.values(state.runs || {}).filter(r => r.status === 'queued').length;

  if (generating) {
    dot.classList.add('active');
    label.textContent = `Generating... (${queued} queued)`;
  } else if (queued > 0) {
    dot.classList.add('active');
    label.textContent = `${queued} queued`;
  } else {
    dot.classList.remove('active');
    label.textContent = 'Idle';
  }
}

async function poll() {
  try {
    const resp = await fetch('/api/state');
    if (!resp.ok) return;
    const state = await resp.json();
    renderGallery(state);
    updateWorkerStatus(state);
  } catch(e) {}
}

async function submitGenerate() {
  const scene = document.getElementById('scene-input').value.trim();
  if (!scene) { showToast('Enter a scene description first.'); return; }
  const model = document.getElementById('model-select').value;
  const faceid = document.getElementById('faceid-toggle').checked;
  const resp = await fetch('/api/generate', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ scene, model, faceid })
  });
  if (resp.ok) {
    const data = await resp.json();
    document.getElementById('scene-input').value = '';
    showToast(`Queued — run ${data.run_id}`);
    poll();
  } else {
    showToast('Error queuing generation');
  }
}

async function approve(id) {
  await fetch(`/api/approve/${id}`, { method: 'POST' });
  showToast('Approved ✅');
  poll();
}

async function unapprove(id) {
  await fetch(`/api/unapprove/${id}`, { method: 'POST' });
  poll();
}

function openIterate(id) {
  iterateTargetId = id;
  document.getElementById('iterate-feedback').value = '';
  document.getElementById('iterate-modal').classList.add('open');
  document.getElementById('iterate-feedback').focus();
}

function closeIterate() {
  document.getElementById('iterate-modal').classList.remove('open');
  iterateTargetId = null;
}

async function submitIterate() {
  const feedback = document.getElementById('iterate-feedback').value.trim();
  if (!feedback) { showToast('Enter feedback first.'); return; }
  const resp = await fetch('/api/iterate', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ id: iterateTargetId, feedback })
  });
  if (resp.ok) {
    const data = await resp.json();
    showToast(`Iteration queued — run ${data.run_id}`);
    closeIterate();
    poll();
  } else {
    showToast('Error queuing iteration');
  }
}

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 3000);
}

// keyboard shortcuts
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeIterate();
  if (e.key === 'Enter' && e.ctrlKey) {
    if (document.getElementById('iterate-modal').classList.contains('open')) submitIterate();
    else submitGenerate();
  }
});

renderPresets();
poll();
pollInterval = setInterval(poll, 3000);

// Persona config
async function loadPersona() {
  const resp = await fetch('/api/persona');
  const cfg = await resp.json();
  document.getElementById('persona-base').value = cfg.base_prompt || '';
  document.getElementById('persona-negative').value = cfg.negative_prompt || '';
}
async function savePersona() {
  const base_prompt = document.getElementById('persona-base').value.trim();
  const negative_prompt = document.getElementById('persona-negative').value.trim();
  const resp = await fetch('/api/persona', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({base_prompt, negative_prompt})
  });
  const data = await resp.json();
  if (data.ok) showToast('Persona saved ✅');
  else showToast('Error saving persona ❌');
}
loadPersona();
</script>
</body>
</html>
""".replace("__PRESETS__", json.dumps(SCENE_PACKS))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # suppress noisy access logs

    def send_json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html: str):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length:
            return json.loads(self.rfile.read(length))
        return {}

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self.send_html(HTML_TEMPLATE)

        elif path == "/api/state":
            self.send_json(load_state())

        elif path == "/api/persona":
            self.send_json(load_persona_config())

        elif path.startswith("/image/"):
            # /image/<run_id>/<filename>
            parts = path.strip("/").split("/", 2)
            if len(parts) == 3:
                _, run_id, filename = parts
                img_path = OUTPUT_DIR / run_id / filename
                if img_path.exists():
                    data = img_path.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "public, max-age=3600")
                    self.end_headers()
                    self.wfile.write(data)
                    return
            self.send_response(404)
            self.end_headers()

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/generate":
            body = self.read_body()
            scene = body.get("scene", "").strip()
            model = body.get("model", DEFAULT_MODEL)
            seed = int(body.get("seed", -1))
            use_faceid = bool(body.get("faceid", False))
            if not scene:
                self.send_json({"error": "scene required"}, 400)
                return
            run_id = enqueue_generation(scene, model=model, seed=seed, use_faceid=use_faceid)
            self.send_json({"run_id": run_id, "status": "queued"})

        elif path == "/api/iterate":
            body = self.read_body()
            parent_id = body.get("id", "").strip()
            feedback = body.get("feedback", "").strip()
            if not parent_id or not feedback:
                self.send_json({"error": "id and feedback required"}, 400)
                return
            state = load_state()
            parent = state["runs"].get(parent_id)
            if not parent:
                self.send_json({"error": "run not found"}, 404)
                return
            new_scene = f"{parent.get('scene', '')} — {feedback}"
            new_version = (parent.get("version") or 1) + 1
            run_id = enqueue_generation(
                new_scene, model=parent.get("model", DEFAULT_MODEL),
                parent_id=parent_id, version=new_version,
            )
            self.send_json({"run_id": run_id, "status": "queued"})

        elif path.startswith("/api/approve/"):
            run_id = path.split("/")[-1]
            state = load_state()
            if run_id not in state["runs"]:
                self.send_json({"error": "not found"}, 404)
                return
            state["runs"][run_id]["approved"] = True
            # Copy to approved dir
            run_dir = OUTPUT_DIR / run_id
            approved_dir = OUTPUT_DIR / "approved"
            approved_dir.mkdir(parents=True, exist_ok=True)
            for img in state["runs"][run_id].get("images", []):
                src = Path(img)
                if src.exists():
                    import shutil
                    shutil.copy2(src, approved_dir / f"{run_id}_{src.name}")
            save_state(state)
            self.send_json({"ok": True})

        elif path == "/api/persona":
            body = self.read_body()
            cfg = load_persona_config()
            if "base_prompt" in body:
                cfg["base_prompt"] = body["base_prompt"].strip()
            if "negative_prompt" in body:
                cfg["negative_prompt"] = body["negative_prompt"].strip()
            save_persona_config(cfg)
            self.send_json({"ok": True, "config": cfg})

        elif path.startswith("/api/unapprove/"):
            run_id = path.split("/")[-1]
            state = load_state()
            if run_id not in state["runs"]:
                self.send_json({"error": "not found"}, 404)
                return
            state["runs"][run_id]["approved"] = False
            save_state(state)
            self.send_json({"ok": True})

        else:
            self.send_response(404)
            self.end_headers()


def main():
    parser = argparse.ArgumentParser(description="Friday Image Pipeline Web UI")
    parser.add_argument("--port", type=int, default=8765, help="Port to serve on (default: 8765)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
    args = parser.parse_args()

    # Start background worker
    t = threading.Thread(target=worker_thread, daemon=True)
    t.start()

    server = HTTPServer((args.host, args.port), Handler)
    print(f"🎨 Friday Image Pipeline running at http://localhost:{args.port}")
    print(f"   ComfyUI: {COMFY_URL}")
    print(f"   Output:  {OUTPUT_DIR.resolve()}")
    print("   Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")


if __name__ == "__main__":
    main()
