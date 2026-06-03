"""Tiny stdlib HTTP server for the local replay/tuning tool.

No framework dependencies. Routes:
  GET  /                      -> the single-page app
  GET  /static/<f>            -> app.js / style.css
  GET  /api/profiles          -> available StyleProfiles
  GET  /api/profile?name=     -> one profile as StyleParams knobs
  POST /api/generate          -> {objects, audio, style, osz, ...}
  GET  /audio?path=           -> audio stream (HTTP Range supported)
  GET  /download?path=        -> the generated .osz
"""

from __future__ import annotations

import json
import mimetypes
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import api

STATIC = os.path.join(os.path.dirname(__file__), "static")
PROFILES_DIR = os.path.abspath("profiles")
OUT_DIR = os.path.abspath("output_web")


class Handler(BaseHTTPRequestHandler):
    server_version = "mp3-to-osu/0.1"

    # -- helpers ------------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str,
              extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"),
                   "application/json")

    def _file(self, path: str, ctype: str | None = None) -> None:
        if not os.path.isfile(path):
            self._json(404, {"error": f"not found: {path}"})
            return
        ctype = ctype or (mimetypes.guess_type(path)[0]
                          or "application/octet-stream")
        size = os.path.getsize(path)
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            s, _, e = rng[6:].partition("-")
            start = int(s) if s else 0
            end = int(e) if e else size - 1
            end = min(end, size - 1)
            start = min(start, end)
            with open(path, "rb") as fh:
                fh.seek(start)
                chunk = fh.read(end - start + 1)
            self.send_response(206)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            self.wfile.write(chunk)
            return
        with open(path, "rb") as fh:
            data = fh.read()
        self._send(200, data, ctype, {"Accept-Ranges": "bytes"})

    # -- routing ------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                self._file(os.path.join(STATIC, "index.html"), "text/html")
            elif u.path.startswith("/static/"):
                # allow nested assets (e.g. /static/sounds/x.wav) while
                # blocking path traversal outside STATIC.
                rel = urllib.parse.unquote(u.path[len("/static/"):])
                full = os.path.normpath(os.path.join(STATIC, rel))
                base = os.path.abspath(STATIC)
                if os.path.abspath(full).startswith(base + os.sep):
                    self._file(full)
                else:
                    self._json(403, {"error": "forbidden"})
            elif u.path == "/api/profiles":
                self._json(200, api.list_profiles(PROFILES_DIR))
            elif u.path == "/api/profile":
                name = (q.get("name") or [None])[0]
                self._json(200, api.profile_params(
                    PROFILES_DIR, name).to_dict())
            elif u.path == "/audio":
                self._file((q.get("path") or [""])[0])
            elif u.path == "/download":
                p = (q.get("path") or [""])[0]
                self._file(p, "application/octet-stream")
            else:
                self._json(404, {"error": "unknown route"})
        except Exception as e:  # noqa: BLE001 - report to the browser
            self._json(500, {"error": repr(e)})

    def _read_body(self, ln: int) -> bytes:
        buf = bytearray()
        while len(buf) < ln:
            chunk = self.rfile.read(min(1 << 20, ln - len(buf)))
            if not chunk:
                break
            buf += chunk
        return bytes(buf)

    def do_POST(self) -> None:  # noqa: N802
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        ln = int(self.headers.get("Content-Length", 0))
        raw = self._read_body(ln)

        if u.path == "/api/upload":
            try:
                name = os.path.basename((q.get("name") or ["upload"])[0])
                name = "".join(c for c in name
                               if c.isalnum() or c in " ._-()") or "upload.mp3"
                up_dir = os.path.join(OUT_DIR, "uploads")
                os.makedirs(up_dir, exist_ok=True)
                dest = os.path.join(up_dir, name)
                with open(dest, "wb") as fh:
                    fh.write(raw)
                self._json(200, {"ok": True, "path": os.path.abspath(dest),
                                 "bytes": len(raw)})
            except Exception as e:  # noqa: BLE001
                self._json(500, {"ok": False, "error": repr(e)})
            return

        if u.path == "/api/import":
            try:
                name = os.path.basename((q.get("name") or ["map.osz"])[0])
                name = "".join(c for c in name
                               if c.isalnum() or c in " ._-()") or "map.osz"
                up_dir = os.path.join(OUT_DIR, "uploads")
                os.makedirs(up_dir, exist_ok=True)
                dest = os.path.join(up_dir, name)
                with open(dest, "wb") as fh:
                    fh.write(raw)
                self._json(200, api.import_osz(dest, OUT_DIR))
            except (FileNotFoundError, RuntimeError) as e:
                self._json(400, {"error": str(e) or repr(e)})
            except Exception as e:  # noqa: BLE001
                self._json(500, {"error": repr(e)})
            return

        if u.path == "/api/replay":
            try:
                name = os.path.basename((q.get("name") or ["r.osr"])[0])
                name = "".join(c for c in name
                               if c.isalnum() or c in " ._-()") or "r.osr"
                up_dir = os.path.join(OUT_DIR, "uploads")
                os.makedirs(up_dir, exist_ok=True)
                dest = os.path.join(up_dir, name)
                with open(dest, "wb") as fh:
                    fh.write(raw)
                from .. import replay
                self._json(200, replay.parse_osr(dest))
            except Exception as e:  # noqa: BLE001
                self._json(200, {"ok": False, "error": repr(e)})
            return

        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "bad json"})
            return
        if u.path == "/api/import_diff":
            try:
                self._json(200, api.import_osz(
                    body["osz"], OUT_DIR, version=body.get("version")))
            except (FileNotFoundError, RuntimeError, KeyError) as e:
                self._json(400, {"error": str(e) or repr(e)})
            except Exception as e:  # noqa: BLE001
                self._json(500, {"error": repr(e)})
            return
        if u.path == "/api/identify":
            try:
                from .. import recognize
                self._json(200, recognize.identify(body["audio_path"]))
            except KeyError:
                self._json(400, {"error": "audio_path required"})
            except Exception as e:  # noqa: BLE001
                self._json(200, {"ok": False, "lyrics": [],
                                 "error": repr(e)})
            return
        if u.path != "/api/generate":
            self._json(404, {"error": "unknown route"})
            return
        try:
            res = api.generate(
                body["audio_path"],
                profiles_dir=PROFILES_DIR,
                profile=body.get("profile"),
                overrides=body.get("overrides") or {},
                difficulty=body.get("difficulty", "hard"),
                out_dir=OUT_DIR,
                lyrics=body.get("lyrics") or None,
                lyrics_drive=bool(body.get("lyrics_drive")),
                tempo=body.get("tempo") or None,
            )
            self._json(200, res)
        except (FileNotFoundError, RuntimeError, KeyError) as e:
            self._json(400, {"error": str(e) or repr(e)})
        except Exception as e:  # noqa: BLE001
            self._json(500, {"error": repr(e)})

    def log_message(self, *_a) -> None:   # quiet console
        pass


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"mp3-to-osu studio: http://{host}:{port}")
    print(f"  profiles dir : {PROFILES_DIR}")
    print(f"  output dir   : {OUT_DIR}")
    print("Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="mp3-to-osu-web")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--profiles", default="profiles",
                    help="Profiles directory (default ./profiles).")
    a = ap.parse_args(argv)
    global PROFILES_DIR
    PROFILES_DIR = os.path.abspath(a.profiles)
    serve(a.host, a.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
