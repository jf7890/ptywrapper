from __future__ import annotations

import json
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


def run_mock_endpoint(host: str, port: int, expected_api_key: str | None = None) -> int:
    events: deque[dict[str, object]] = deque(maxlen=200)
    events_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path in {"", "/"}:
                html = _build_dashboard_html()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))
                return

            if parsed.path == "/events":
                with events_lock:
                    payload = list(events)
                encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(encoded)
                return

            if parsed.path == "/health":
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
                return

            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not found")

        def do_POST(self) -> None:  # noqa: N802
            if expected_api_key is not None:
                expected = f"Bearer {expected_api_key}"
                if self.headers.get("Authorization") != expected:
                    self.send_response(401)
                    self.end_headers()
                    self.wfile.write(b"unauthorized")
                    return

            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            try:
                payload = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"invalid json")
                return

            with events_lock:
                if isinstance(payload, dict):
                    events.appendleft(payload)
                else:
                    events.appendleft({"raw": payload})

            print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
            self.send_response(202)
            self.end_headers()
            self.wfile.write(b"accepted")

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Mock endpoint listening on http://{host}:{port}", flush=True)
    print(f"Dashboard: http://{host}:{port}/", flush=True)
    print(f"Health: http://{host}:{port}/health", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


def _build_dashboard_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Cyber Shell Mock Endpoint</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f4f7fb;
      --panel: #ffffff;
      --ink: #1a2233;
      --muted: #61708f;
      --accent: #1769e0;
      --border: #d9e2f2;
    }
    body {
      margin: 0;
      background: radial-gradient(circle at top right, #dff0ff, var(--bg) 36%);
      color: var(--ink);
      font-family: "Segoe UI", "Tahoma", sans-serif;
    }
    .wrap {
      max-width: 980px;
      margin: 0 auto;
      padding: 24px;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      box-shadow: 0 10px 24px rgba(17, 42, 91, 0.08);
      padding: 16px;
    }
    h1 {
      margin: 0 0 8px 0;
      font-size: 24px;
    }
    p {
      margin: 0 0 14px 0;
      color: var(--muted);
    }
    .toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 12px;
      gap: 8px;
    }
    .badge {
      display: inline-block;
      padding: 3px 10px;
      border-radius: 999px;
      background: #e7f1ff;
      color: #0f4baa;
      font-size: 13px;
      font-weight: 600;
    }
    button {
      border: 1px solid #b9ccee;
      background: white;
      color: #10386f;
      border-radius: 8px;
      padding: 6px 10px;
      font-weight: 600;
      cursor: pointer;
    }
    button:hover {
      border-color: #8fb0e4;
    }
    pre {
      margin: 0;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: #fbfdff;
      padding: 12px;
      max-height: 68vh;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: "Consolas", "Courier New", monospace;
      font-size: 12px;
      line-height: 1.35;
    }
    .hint code {
      color: var(--accent);
      font-weight: 700;
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>Cyber Shell Mock Endpoint</h1>
      <p class="hint">This endpoint accepts events via <code>POST /api/terminal-events</code> or any other POST path. The dashboard refreshes every second.</p>
      <div class="toolbar">
        <span id="count" class="badge">0 events</span>
        <button id="refresh">Refresh now</button>
      </div>
      <pre id="events">Waiting for events...</pre>
    </div>
  </div>
  <script>
    const eventsEl = document.getElementById('events');
    const countEl = document.getElementById('count');
    const refreshBtn = document.getElementById('refresh');

    async function renderEvents() {
      try {
        const res = await fetch('/events', { cache: 'no-store' });
        const data = await res.json();
        countEl.textContent = `${data.length} events`;
        eventsEl.textContent = JSON.stringify(data, null, 2);
      } catch (err) {
        eventsEl.textContent = `Fetch failed: ${String(err)}`;
      }
    }

    refreshBtn.addEventListener('click', renderEvents);
    renderEvents();
    setInterval(renderEvents, 1000);
  </script>
</body>
</html>
"""
