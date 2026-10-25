"""Minimal ntfy look-alike for tests: POST /<topic>, GET /<topic>/json?poll=1&since=, GET /<topic>/sse?since="""
import json, threading, time, uuid, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

LOG = {}          # topic -> list of events
FAIL = {"n": 0}   # reject the next n publishes with HTTP 429 (set via GET /_fail?n=3)
COND = threading.Condition()

def after(topic, since):
    evs = LOG.get(topic, [])
    if since in (None, "", "all"):
        return list(evs)
    for i, e in enumerate(evs):
        if e["id"] == since:
            return evs[i + 1:]
    return list(evs) if since.endswith("h") else []

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def do_POST(self):
        topic = urlparse(self.path).path.strip("/")
        if FAIL["n"] > 0:
            FAIL["n"] -= 1
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(429); self.send_header("Content-Length", "0"); self.end_headers(); return
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        if len(body) > 4096:
            self.send_response(413); self.send_header("Content-Length", "0"); self.end_headers(); return
        ev = {"id": uuid.uuid4().hex[:12], "time": int(time.time()), "event": "message", "topic": topic, "message": body}
        with COND:
            LOG.setdefault(topic, []).append(ev); COND.notify_all()
        out = json.dumps(ev).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        if u.path == "/_fail":
            FAIL["n"] = int((q.get("n") or ["0"])[0])
            self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"ok"); return
        parts = u.path.strip("/").split("/")
        topic, kind = parts[0], parts[1] if len(parts) > 1 else "json"
        since = (q.get("since") or ["all"])[0]
        if kind == "json":
            data = "".join(json.dumps(e) + "\n" for e in after(topic, since)).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data); return
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Connection", "close"); self.end_headers()
        self.wfile.write(b'event: open\ndata: {"event":"open"}\n\n'); self.wfile.flush()
        last = since
        try:
            while True:
                with COND:
                    evs = after(topic, last)
                    if not evs: COND.wait(timeout=5)
                    evs = after(topic, last)
                if not evs:
                    self.wfile.write(b'event: keepalive\ndata: {"event":"keepalive"}\n\n'); self.wfile.flush(); continue
                for e in evs:
                    self.wfile.write(f"event: message\ndata: {json.dumps(e)}\n\n".encode()); last = e["id"]
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

def serve(port):
    srv = ThreadingHTTPServer(("127.0.0.1", port), H); srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start(); return srv

if __name__ == "__main__":
    serve(int(sys.argv[1])); 
    while True: time.sleep(3600)
