"""The browser editor: a small HTTP server inside the UI process.

    browser ──HTTP──> request thread ──run_on_ui──> EditorController (UI thread)

A thread in the UI process, not a service of its own, so it inherits the UI's
core (0) and priority (Nice=5) and can never land on an audio core. It talks to
nothing but the controller: no JACK, no MIDI, and mod-host only through the
same EngineManager calls a touch makes.

**Off unless switched on**, from the settings screen, and off again at the
next boot or after `EDITOR_IDLE_S` without a request. Everything under /api/
needs a session, which is had by entering the PIN shown next to the switch; the
PIN is new each time the editor starts, and replaced after too many wrong
guesses. Standard library only — this box carries no web framework for one page.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import secrets
import socket
import threading
import time
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

from synth_ui.server import EditorError

if TYPE_CHECKING:
    from synth_ui.ui.editor import EditorController

logger = logging.getLogger(__name__)

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")
COOKIE = "synth_session"
# Four digits is 10,000 guesses. Replacing the PIN after this many wrong ones
# means guessing has to start over, and the new one is only on the box's screen.
MAX_BAD_PINS = 10
MAX_BODY = 2 * 1024 * 1024

Dispatch = Callable[[Callable], object]


class EditorServer:
    def __init__(
        self,
        controller: EditorController,
        dispatch: Dispatch,
        port: int = 8080,
        host: str = "0.0.0.0",
    ):
        self.controller = controller
        # Runs a callable on the UI thread and returns its result.
        self.dispatch = dispatch
        self.port = port
        self.host = host
        self.pin = _new_pin()
        self._sessions: set[str] = set()
        self._bad_pins = 0
        self._lock = threading.Lock()
        self._last_request = time.monotonic()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._routes = _routes(self)

    # --- lifecycle --------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._httpd is not None

    @property
    def url(self) -> str:
        # By name, like everything else on this box: avahi answers for it, and
        # the address changes with the network.
        return f"http://{socket.gethostname()}.local:{self.port}"

    def start(self) -> bool:
        handler = type("Handler", (_Handler,), {"editor": self})
        try:
            self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        except OSError:
            logger.exception("editor could not bind port %d", self.port)
            return False
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self._last_request = time.monotonic()
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="editor", daemon=True
        )
        self._thread.start()
        logger.info("editor listening on %s", self.url)
        return True

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        with self._lock:
            self._sessions.clear()

    def idle_for(self, now: float) -> float:
        return now - self._last_request

    # --- sessions ---------------------------------------------------------

    def login(self, pin: str) -> str | None:
        with self._lock:
            if secrets.compare_digest(str(pin), self.pin):
                token = secrets.token_hex(16)
                self._sessions.add(token)
                self._bad_pins = 0
                return token
            self._bad_pins += 1
            if self._bad_pins >= MAX_BAD_PINS:
                logger.warning("editor: %d wrong PINs; replacing it", MAX_BAD_PINS)
                self.pin = _new_pin()
                self._bad_pins = 0
            return None

    def authorised(self, token: str | None) -> bool:
        with self._lock:
            return token is not None and token in self._sessions

    def touch(self) -> None:
        self._last_request = time.monotonic()


def _new_pin() -> str:
    return f"{secrets.randbelow(10000):04d}"


# --- routes ------------------------------------------------------------------

def _routes(server: EditorServer):
    c = server.controller

    def ui(fn):
        """Run on the UI thread — anything that reads or changes app state."""
        return lambda *args: server.dispatch(lambda: fn(*args))

    # (method, pattern, handler(match_groups..., body, query) -> result)
    return [
        ("GET", r"/api/state", ui(lambda body, q: c.state())),
        ("GET", r"/api/catalog", ui(lambda body, q: c.catalog())),
        ("GET", r"/api/export", ui(lambda body, q: c.export())),
        ("POST", r"/api/import", ui(lambda body, q: c.import_sets(body))),
        # lv2info, cached: safe and better off the UI thread (see editor.py).
        ("GET", r"/api/controls/effect",
         lambda body, q: c.effect_controls(_q(q, "uri"))),
        ("GET", r"/api/controls/voice",
         lambda body, q: c.voice_controls(_q(q, "name"))),

        ("POST", r"/api/sets", ui(lambda body, q: c.create_set(body.get("name", "")))),
        ("PATCH", r"/api/sets/([\w-]+)",
         ui(lambda sid, body, q: c.rename_set(sid, body.get("name", "")))),
        ("DELETE", r"/api/sets/([\w-]+)", ui(lambda sid, body, q: c.delete_set(sid))),
        ("POST", r"/api/sets/([\w-]+)/move",
         ui(lambda sid, body, q: c.move_set(sid, body["to"]))),
        ("POST", r"/api/sets/([\w-]+)/rigs",
         ui(lambda sid, body, q: c.create_rig(
             sid, body["voice"], body.get("name", "")))),

        ("PATCH", r"/api/rigs/([\w-]+)",
         ui(lambda rid, body, q: c.update_rig(rid, **_pick(
             body, "name", "voice", "trim_db", "fixed_velocity")))),
        ("DELETE", r"/api/rigs/([\w-]+)", ui(lambda rid, body, q: c.delete_rig(rid))),
        ("POST", r"/api/rigs/([\w-]+)/move",
         ui(lambda rid, body, q: c.move_rig(rid, body["to"]))),
        ("POST", r"/api/rigs/([\w-]+)/copy",
         ui(lambda rid, body, q: c.copy_rig(rid, body["set_id"]))),
        ("PUT", r"/api/rigs/([\w-]+)/voice-params/([\w.-]+)",
         ui(lambda rid, sym, body, q: c.set_voice_param(rid, sym, body["value"]))),
        ("POST", r"/api/rigs/([\w-]+)/effects",
         ui(lambda rid, body, q: c.add_effect(rid, body["uri"], body.get("index")))),
        ("PATCH", r"/api/rigs/([\w-]+)/effects/(\d+)",
         ui(lambda rid, i, body, q: c.update_effect(
             rid, i, **_pick(body, "bypassed", "params")))),
        ("DELETE", r"/api/rigs/([\w-]+)/effects/(\d+)",
         ui(lambda rid, i, body, q: c.remove_effect(rid, i))),
        ("POST", r"/api/rigs/([\w-]+)/effects/(\d+)/move",
         ui(lambda rid, i, body, q: c.move_effect(rid, i, body["to"]))),
    ]


def _q(query: dict, key: str) -> str:
    values = query.get(key)
    if not values:
        raise EditorError(f"missing ?{key}=")
    return values[0]


def _pick(body: dict, *keys: str) -> dict:
    return {k: body[k] for k in keys if k in body}


class _Handler(BaseHTTPRequestHandler):
    editor: EditorServer
    server_version = "synth-editor"

    def log_message(self, fmt, *args):   # journald, not stderr noise per request
        logger.debug("editor %s " + fmt, self.address_string(), *args)

    # --- plumbing ---------------------------------------------------------

    def _send(self, status: int, payload=None, headers: dict | None = None) -> None:
        body = b"" if payload is None else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise EditorError("request too large", status=413)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except ValueError as exc:
            raise EditorError("body is not JSON") from exc

    def _session(self) -> str | None:
        for part in (self.headers.get("Cookie") or "").split(";"):
            key, _, value = part.strip().partition("=")
            if key == COOKIE:
                return value
        return None

    # --- verbs ------------------------------------------------------------

    def do_GET(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            return self._static(path)
        self._api("GET")

    def do_POST(self):
        self._api("POST")

    def do_PATCH(self):
        self._api("PATCH")

    def do_PUT(self):
        self._api("PUT")

    def do_DELETE(self):
        self._api("DELETE")

    def _static(self, path: str) -> None:
        name = "index.html" if path in ("", "/") else path.lstrip("/")
        # One flat directory, no subpaths: nothing outside it is reachable.
        if "/" in name or name.startswith("."):
            return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
        full = os.path.join(WEB_DIR, name)
        if not os.path.isfile(full):
            return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(HTTPStatus.OK)
        self.send_header(
            "Content-Type", mimetypes.guess_type(name)[0] or "application/octet-stream"
        )
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _api(self, method: str) -> None:
        editor = self.editor
        editor.touch()
        url = urlparse(self.path)
        try:
            body = self._body() if method != "GET" else {}
            if method == "POST" and url.path == "/api/login":
                token = editor.login(body.get("pin", "") if isinstance(body, dict)
                                     else "")
                if token is None:
                    return self._send(HTTPStatus.UNAUTHORIZED, {"error": "wrong PIN"})
                return self._send(
                    HTTPStatus.OK, {"ok": True},
                    {"Set-Cookie": f"{COOKIE}={token}; Path=/; HttpOnly; "
                                   "SameSite=Strict"},
                )
            if not editor.authorised(self._session()):
                return self._send(HTTPStatus.UNAUTHORIZED, {"error": "PIN needed"})
            for route_method, pattern, handler in editor._routes:
                if route_method != method:
                    continue
                match = re.fullmatch(pattern, url.path)
                if match:
                    if not isinstance(body, (dict, list)):
                        raise EditorError("body must be a JSON object")
                    result = handler(*match.groups(), body, parse_qs(url.query))
                    return self._send(HTTPStatus.OK, {} if result is None else result)
            self._send(HTTPStatus.NOT_FOUND, {"error": "no such endpoint"})
        except EditorError as exc:
            self._send(exc.status, {"error": str(exc)})
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            self._send(HTTPStatus.BAD_REQUEST, {"error": f"bad request: {exc}"})
        except TimeoutError:
            self._send(
                HTTPStatus.SERVICE_UNAVAILABLE, {"error": "the instrument is busy"}
            )
        except Exception:
            logger.exception("editor request failed: %s %s", method, self.path)
            self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal error"})
