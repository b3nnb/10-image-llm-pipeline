#!/usr/bin/env python3
"""
Friday Image Gallery — static HTML viewer for pipeline runs.

Usage:
  python gallery.py                     # generate gallery.html (open in browser)
  python gallery.py --output /tmp/g.html
  python gallery.py --serve             # serve on http://localhost:8765 (auto-refresh)
"""

import argparse
import json
import os
import shutil
from pathlib import Path
from datetime import datetime

OUTPUT_DIR = Path(os.environ.get("PIPELINE_OUTPUT_DIR", "./output"))
STATE_FILE = OUTPUT_DIR / "state.json"


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"runs": {}}


def image_to_data_uri(img_path: Path) -> str | None:
    """Read image and return as base64 data URI for self-contained HTML."""
    if not img_path.exists():
        return None
    import base64
    suffix = img_path.suffix.lower()
    mime = {"jpg": "image/jpeg", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".webp": "image/webp"}.get(suffix, "image/jpeg")
    with open(img_path, "rb") as f:
        data = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{data}"


def img_relative_path(img_path: str, gallery_dir: Path) -> str:
    """Return a path usable from the gallery HTML file location."""
    src = Path(img_path)
    try:
        return str(src.resolve().relative_to(gallery_dir.resolve()))
    except ValueError:
        return str(src.resolve())


def render_gallery(state: dict, gallery_path: Path, embed_images: bool = False) -> int:
    """Generate HTML gallery. Returns number of runs rendered."""
    runs = sorted(
        state.get("runs", {}).values(),
        key=lambda r: r.get("created_at", ""),
        reverse=True,
    )

    cards_html = []
    for run in runs:
        run_id = run["id"]
        approved = run.get("approved", False)
        status = run.get("status", "?")
        version = run.get("version", 1)
        scene = run.get("scene", "")
        model = run.get("model", "")
        seed = run.get("seed", "?")
        ts = run.get("created_at", "")[:16].replace("T", " ")
        parent_id = run.get("parent_id")
        feedback_history = run.get("feedback_history", [])

        # Images
        img_tags = []
        for img_path in run.get("images", []):
            p = Path(img_path)
            if embed_images:
                uri = image_to_data_uri(p)
                if uri:
                    img_tags.append(f'<img src="{uri}" alt="{scene[:40]}" loading="lazy">')
            else:
                rel = img_relative_path(img_path, gallery_path.parent)
                img_tags.append(f'<img src="{rel}" alt="{scene[:40]}" loading="lazy">')

        if not img_tags:
            img_tags = ['<div class="no-img">No images</div>']

        approved_badge = '<span class="badge approved">✅ Approved</span>' if approved else ""
        version_badge = f'<span class="badge version">v{version}</span>'
        status_badge = f'<span class="badge status-{status}">{status}</span>'
        parent_note = f'<div class="parent-note">🔄 Iterated from <code>{parent_id}</code></div>' if parent_id else ""
        feedback_note = ""
        if feedback_history:
            fb_items = "".join(f"<li>{fb}</li>" for fb in feedback_history)
            feedback_note = f'<div class="feedback-history"><strong>Feedback:</strong><ul>{fb_items}</ul></div>'

        cards_html.append(f"""
<div class="card{'  approved' if approved else ''}" id="run-{run_id}">
  <div class="card-images">
    {"".join(img_tags)}
  </div>
  <div class="card-meta">
    <div class="badges">{version_badge} {status_badge} {approved_badge}</div>
    <div class="run-id"><code>{run_id}</code> <span class="ts">{ts}</span></div>
    <div class="scene">{scene}</div>
    <div class="model-info">Model: <strong>{model}</strong> · Seed: <code>{seed}</code></div>
    {parent_note}
    {feedback_note}
    <div class="actions">
      <button onclick="copyCmd('approve', '{run_id}')" title="Copy approve command">📋 Approve</button>
      <button onclick="copyCmd('iterate', '{run_id}')" title="Copy iterate command">🔄 Iterate</button>
      <button onclick="copyCmd('export', '{run_id}')" title="Copy export command">📤 Export</button>
    </div>
  </div>
</div>""")

    total = len(runs)
    approved_count = sum(1 for r in runs if r.get("approved"))
    generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Friday Image Pipeline Gallery</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: #0d1117;
      color: #e6edf3;
      min-height: 100vh;
    }}
    header {{
      background: #161b22;
      border-bottom: 1px solid #30363d;
      padding: 1.25rem 2rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}
    header h1 {{ font-size: 1.3rem; color: #c9d1d9; }}
    header .stats {{ font-size: 0.85rem; color: #8b949e; }}
    .filter-bar {{
      padding: 1rem 2rem;
      background: #161b22;
      border-bottom: 1px solid #21262d;
      display: flex; gap: 0.75rem; flex-wrap: wrap; align-items: center;
    }}
    .filter-bar button {{
      background: #21262d; color: #8b949e; border: 1px solid #30363d;
      padding: 0.35rem 0.85rem; border-radius: 6px; cursor: pointer; font-size: 0.82rem;
    }}
    .filter-bar button:hover, .filter-bar button.active {{
      background: #388bfd22; color: #79c0ff; border-color: #388bfd;
    }}
    .gallery {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
      gap: 1.25rem;
      padding: 1.5rem 2rem;
    }}
    .card {{
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 10px;
      overflow: hidden;
      transition: border-color 0.15s;
    }}
    .card:hover {{ border-color: #388bfd66; }}
    .card.approved {{ border-color: #3fb95066; }}
    .card-images {{
      background: #0d1117;
      min-height: 200px;
      display: flex;
      flex-wrap: wrap;
      gap: 2px;
      align-items: center;
      justify-content: center;
    }}
    .card-images img {{
      max-width: 100%;
      max-height: 340px;
      object-fit: cover;
      flex: 1;
    }}
    .no-img {{
      color: #8b949e; font-size: 0.85rem; padding: 2rem;
    }}
    .card-meta {{
      padding: 0.85rem 1rem;
    }}
    .badges {{ display: flex; gap: 0.4rem; flex-wrap: wrap; margin-bottom: 0.4rem; }}
    .badge {{
      font-size: 0.72rem;
      padding: 0.18rem 0.55rem;
      border-radius: 4px;
      font-weight: 600;
    }}
    .badge.version {{ background: #21262d; color: #8b949e; border: 1px solid #30363d; }}
    .badge.approved {{ background: #1a4731; color: #3fb950; border: 1px solid #3fb95066; }}
    .badge.status-done {{ background: #1a3a6e; color: #79c0ff; border: 1px solid #388bfd66; }}
    .badge.status-generating {{ background: #4d3a00; color: #f0883e; border: 1px solid #f0883e66; }}
    .badge.status-timeout {{ background: #3d1a1a; color: #f85149; border: 1px solid #f8514966; }}
    .run-id {{ font-size: 0.78rem; color: #8b949e; margin-bottom: 0.35rem; }}
    .run-id code {{ color: #58a6ff; font-size: 0.78rem; }}
    .ts {{ color: #484f58; }}
    .scene {{ font-size: 0.88rem; color: #c9d1d9; margin-bottom: 0.4rem; line-height: 1.4; }}
    .model-info {{ font-size: 0.78rem; color: #8b949e; margin-bottom: 0.35rem; }}
    .model-info code {{ color: #e3b341; font-size: 0.76rem; }}
    .parent-note {{ font-size: 0.78rem; color: #8b949e; margin-bottom: 0.3rem; }}
    .parent-note code {{ color: #58a6ff; }}
    .feedback-history {{ font-size: 0.78rem; color: #8b949e; margin-bottom: 0.4rem; }}
    .feedback-history ul {{ margin-top: 0.2rem; padding-left: 1.2rem; }}
    .actions {{ display: flex; gap: 0.5rem; flex-wrap: wrap; margin-top: 0.6rem; }}
    .actions button {{
      background: #21262d; color: #8b949e; border: 1px solid #30363d;
      padding: 0.3rem 0.7rem; border-radius: 6px; cursor: pointer; font-size: 0.78rem;
    }}
    .actions button:hover {{ background: #30363d; color: #c9d1d9; }}
    #toast {{
      position: fixed; bottom: 1.5rem; right: 1.5rem;
      background: #1f6feb; color: #fff;
      padding: 0.65rem 1.25rem; border-radius: 8px;
      font-size: 0.85rem; display: none; z-index: 999;
      box-shadow: 0 4px 12px rgba(0,0,0,0.4);
    }}
    footer {{
      padding: 1rem 2rem;
      font-size: 0.75rem;
      color: #484f58;
      border-top: 1px solid #21262d;
      text-align: center;
    }}
    @media (max-width: 600px) {{
      .gallery {{ grid-template-columns: 1fr; padding: 1rem; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>🎨 Friday Image Pipeline</h1>
    <span class="stats">{total} runs · {approved_count} approved · {generated_at}</span>
  </header>
  <div class="filter-bar">
    <button class="active" onclick="filterCards('all', this)">All</button>
    <button onclick="filterCards('approved', this)">✅ Approved only</button>
    <button onclick="filterCards('unapproved', this)">⏳ Not approved</button>
    <button onclick="filterCards('done', this)">Done</button>
  </div>
  <div class="gallery" id="gallery">
    {"".join(cards_html)}
  </div>
  <div id="toast"></div>
  <footer>Generated by Friday Image Pipeline · python gallery.py</footer>

  <script>
    function copyCmd(action, id) {{
      let cmd = '';
      if (action === 'approve')  cmd = `python pipeline.py approve --id ${{id}}`;
      if (action === 'iterate')  cmd = `python pipeline.py iterate --id ${{id}} --feedback "..."`;
      if (action === 'export')   cmd = `python pipeline.py export --id ${{id}} --name my-export`;
      navigator.clipboard.writeText(cmd).catch(() => {{
        const t = document.createElement('textarea');
        t.value = cmd; document.body.appendChild(t);
        t.select(); document.execCommand('copy');
        document.body.removeChild(t);
      }});
      const toast = document.getElementById('toast');
      toast.textContent = '📋 Copied: ' + cmd;
      toast.style.display = 'block';
      setTimeout(() => toast.style.display = 'none', 2500);
    }}

    function filterCards(mode, btn) {{
      document.querySelectorAll('.filter-bar button').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      document.querySelectorAll('.card').forEach(card => {{
        const approved = card.classList.contains('approved');
        const done = card.querySelector('.badge.status-done') !== null;
        let show = true;
        if (mode === 'approved')   show = approved;
        if (mode === 'unapproved') show = !approved;
        if (mode === 'done')       show = done;
        card.style.display = show ? '' : 'none';
      }});
    }}
  </script>
</body>
</html>
"""
    gallery_path.parent.mkdir(parents=True, exist_ok=True)
    gallery_path.write_text(html, encoding="utf-8")
    return total


def main():
    parser = argparse.ArgumentParser(description="Friday Image Gallery Generator")
    parser.add_argument("--output", default="gallery.html", help="Output HTML file path")
    parser.add_argument("--embed", action="store_true", help="Embed images as base64 (self-contained)")
    parser.add_argument("--serve", action="store_true", help="Serve gallery on localhost:8765 with auto-refresh")
    args = parser.parse_args()

    gallery_path = Path(args.output).resolve()

    state = load_state()
    total = render_gallery(state, gallery_path, embed_images=args.embed)
    print(f"✅ Gallery generated: {gallery_path} ({total} runs)")

    if args.serve:
        import http.server
        import threading
        import webbrowser

        gallery_dir = gallery_path.parent
        os.chdir(gallery_dir)

        port = 8765
        handler = http.server.SimpleHTTPRequestHandler

        class QuietHandler(handler):
            def log_message(self, format, *a):  # noqa: A002
                pass

        httpd = http.server.HTTPServer(("", port), QuietHandler)
        url = f"http://localhost:{port}/{gallery_path.name}"
        print(f"🌐 Serving at {url}")
        print("   Press Ctrl+C to stop")
        webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n👋 Server stopped")


if __name__ == "__main__":
    main()
