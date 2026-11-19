"""The "Petdex" plugin process: search in the pet catalog (catalog.py) and the choice of the hub's pet.

The page talks to it on 127.0.0.1:<page port + 9>: GET /search?q=&kind= — pets, POST /choose {"slug"} —
choose one, GET /sprite/<slug> and /thumb/<slug> — images (downloaded from assets.petdex.dev once). The choice goes as a post to the plugin channel (EXTENDING.md, API 1.2), any hub page sees it, a phone too.
Only requests from the hub page of this machine are accepted (Origin). It does not change the core, it writes only facts to the journal."""
import http.server
import json
import os
import threading
import urllib.parse

from core import spool

from catalog import Catalog, Images

STATE = os.environ.get("HUB_STATE_DIR", "")
NAME = os.environ.get("HUB_EXTENSION", "petdex")
CHANNEL = NAME.split(":")[-1]
WEB_PORT = int(os.environ.get("HUB_WEB_PORT", "8787"))
PORT = WEB_PORT + 9
ORIGINS = (f"http://127.0.0.1:{WEB_PORT}", f"http://localhost:{WEB_PORT}")


class Plugin:
    def __init__(self, state_dir, catalog=None, put=None):
        self.catalog = catalog or Catalog(os.path.join(state_dir, "petdex", "manifest.json"))
        self.images = Images(os.path.join(state_dir, "petdex"), self.catalog)
        self.put = put or (lambda data: spool.put(os.path.join(state_dir, "spool"), "ext.post", None, data,
                                                   src=f"ext:{NAME}"))
        self.lock = threading.Lock()

    def image(self, path):
        """GET /sprite/<slug>, /thumb/<slug> -> (bytes, type) or None. Images go without the Origin check: <img> does not send it,
        and we serve only public images of the catalog's pets, and only on 127.0.0.1."""
        parts = urllib.parse.urlparse(path).path.strip("/").split("/")
        if len(parts) != 2:
            return None
        return self.images.get(parts[0], urllib.parse.unquote(parts[1]))   # in parallel: files are written through .tmp

    def act(self, method, path, origin, body=None):
        """A page request -> (code, answer)."""
        if origin not in ORIGINS:
            return 403, {"error": "only from the hub page"}
        url = urllib.parse.urlparse(path)
        q = urllib.parse.parse_qs(url.query)
        with self.lock:
            if method == "GET" and url.path == "/search":
                pets = self.catalog.search((q.get("q") or [""])[0], (q.get("kind") or [""])[0])
                return 200, {"pets": pets, "total": len(self.catalog.load())}
            if method == "POST" and url.path == "/choose":
                pet = self.catalog.get(str((body or {}).get("slug") or ""))
                if not pet:
                    return 404, {"error": "no such pet in Petdex"}
                self.put({"plugin": CHANNEL, "text": json.dumps(pet, ensure_ascii=False), "channel": "plugin",
                          "owner": False, "sender": NAME})
                return 200, {"pet": pet}
        return 404, {"error": "not found"}


def serve(plugin):
    class Handler(http.server.BaseHTTPRequestHandler):
        def answer(self, method):
            origin = self.headers.get("Origin") or ""
            body = None
            if method == "POST":
                try:
                    body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 4096)) or b"{}")
                except ValueError:
                    body = {}
            code, out = plugin.act(method, self.path, origin, body)
            data = json.dumps(out, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            if origin in ORIGINS:
                self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.startswith(("/sprite/", "/thumb/")):
                got = plugin.image(self.path)
                self.send_response(200 if got else 404)
                if got:
                    self.send_header("Content-Type", got[1])
                    self.send_header("Cache-Control", "max-age=86400")
                    self.send_header("Content-Length", str(len(got[0])))
                self.end_headers()
                if got:
                    self.wfile.write(got[0])
                return
            self.answer("GET")

        def do_POST(self):
            self.answer("POST")

        def do_OPTIONS(self):   # a POST with JSON — the browser asks for permission first
            origin = self.headers.get("Origin") or ""
            self.send_response(204 if origin in ORIGINS else 403)
            if origin in ORIGINS:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Methods", "GET, POST")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    print(f"petdex: channel {CHANNEL}, page — 127.0.0.1:{PORT}", flush=True)
    serve(Plugin(STATE))
