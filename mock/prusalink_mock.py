#!/usr/bin/env python3
"""A stand-in PrusaLink printer for building and testing the widget.

Serves the read-only part of the PrusaLink v1 API that the widget uses,
shaped after Prusa's published OpenAPI document
(github.com/prusa3d/Prusa-Link-Web, spec/openapi.yaml):

    GET /api/version      firmware and API versions
    GET /api/v1/info      printer name, nozzle, serial
    GET /api/v1/status    state, temperatures, job telemetry
    GET /api/v1/job       the current job and its file (204 when none)
    GET /api/thumbnails/...  the model preview the job's file refers to (PNG)

Both ways a client can sign in are accepted: an ``X-Api-Key`` header, and
HTTP Digest (RFC 7616) with a username and the password. The key is the
password unless ``--api-key`` sets a separate one, as the Raspberry Pi
PrusaLink has. ``--auth`` narrows sign-in to one of the two.

Two habits are copied from a real Raspberry Pi PrusaLink 0.8.1, because
they are easy to trip over: a request carrying an ``X-Api-Key`` it doesn't
recognise is refused with 403 even beside a valid Digest response, and the
Digest algorithm is "MD5-sess", which the answer must repeat exactly.

Run it:

    python3 mock/prusalink_mock.py --scenario printing --live

then point the widget at http://127.0.0.1:8017 with the password ``mock``.
Switch what the printer is doing without restarting:

    curl -X POST http://127.0.0.1:8017/_mock/scenario/paused

Standard library only. It is a mock: field names and types follow the
OpenAPI document, the values are invented, and nothing here has been compared
against a physical printer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import struct
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote

SCENARIOS = (
    "idle",
    "ready",
    "busy",
    "printing",
    "paused",
    "attention",
    "finished",
    "stopped",
    "error",
)

# Scenarios in which the printer holds a job.
_WITH_JOB = {"printing", "paused", "attention", "finished", "stopped"}
# Scenarios in which the heaters are at printing temperature.
_HOT = {"printing", "paused", "attention", "busy"}

REALM = "Printer API"
DEFAULT_PORT = 8017
DEFAULT_PASSWORD = "mock"
DEFAULT_USERNAME = "maker"
DEFAULT_FILE = "Tesserae_Frame_Stand_0.2mm_PLA_MK3.5S_3h12m.bgcode"


def _inside(px: float, py: float, quad: tuple[tuple[float, float], ...]) -> bool:
    """Whether a point is inside a convex polygon (same side of every edge)."""
    sign = 0
    for i, (x1, y1) in enumerate(quad):
        x2, y2 = quad[(i + 1) % len(quad)]
        cross = (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)
        if cross:
            if sign and (cross > 0) != (sign > 0):
                return False
            sign = 1 if cross > 0 else -1
    return True


def _preview_png(width: int = 440, height: int = 240) -> bytes:
    """A stand-in for a slicer preview: an orange block on a transparent
    background, the way PrusaSlicer draws them. Built by hand so the mock
    needs no imaging library."""
    cx, cy, s = width / 2, height / 2, height * 0.47
    dx = s * 0.866
    top, right_top, right_bottom = (
        (cx, cy - s),
        (cx + dx, cy - s / 2),
        (cx + dx, cy + s / 2),
    )
    bottom, left_bottom, left_top = (
        (cx, cy + s),
        (cx - dx, cy + s / 2),
        (cx - dx, cy - s / 2),
    )
    centre = (cx, cy)
    faces = (
        ((top, right_top, centre, left_top), (252, 138, 84)),  # top, lit
        ((left_top, centre, bottom, left_bottom), (250, 104, 49)),  # left
        ((centre, right_top, right_bottom, bottom), (196, 72, 28)),  # right, shaded
    )
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # PNG filter type: none
        for x in range(width):
            pixel = (0, 0, 0, 0)
            for quad, (r, g, b) in faces:
                if _inside(x + 0.5, y + 0.5, quad):
                    pixel = (r, g, b, 255)
                    break
            rows.extend(pixel)

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + chunk(b"IEND", b"")
    )


class MockPrinter:
    """The state behind the endpoints. One instance per server."""

    def __init__(
        self,
        scenario: str = "printing",
        *,
        username: str = DEFAULT_USERNAME,
        password: str = DEFAULT_PASSWORD,
        auth: str = "both",
        progress: float = 42.0,
        duration_s: int = 3 * 3600 + 12 * 60,
        live: bool = False,
        name: str = "Original Prusa MK3.5S",
        file_name: str = DEFAULT_FILE,
        digest_algorithm: str = "MD5-sess",
        thumbnail: bool = True,
        api_key: str | None = None,
    ) -> None:
        if scenario not in SCENARIOS:
            raise ValueError(
                f"unknown scenario {scenario!r}; pick one of {', '.join(SCENARIOS)}"
            )
        self.scenario = scenario
        self.username = username
        self.password = password
        self.auth = auth
        self.duration_s = max(60, int(duration_s))
        self.live = live
        self.name = name
        self.file_name = file_name
        self.digest_algorithm = digest_algorithm
        self.thumbnail = thumbnail
        self.api_key = api_key if api_key is not None else password
        self._png: bytes | None = None
        self.job_id = 420
        self._progress = max(0.0, min(100.0, float(progress)))
        self._started = time.monotonic()
        self._lock = threading.Lock()
        self.nonce = secrets.token_hex(16)

    # -- simulated telemetry -------------------------------------------

    def set_scenario(self, scenario: str) -> None:
        if scenario not in SCENARIOS:
            raise ValueError(scenario)
        with self._lock:
            self.scenario = scenario

    def progress(self) -> float:
        """Percent done. With ``live`` it climbs with the wall clock while
        the scenario is ``printing``; otherwise it stays where it was set."""
        with self._lock:
            if self.scenario == "finished":
                return 100.0
            if self.live and self.scenario == "printing":
                gained = (time.monotonic() - self._started) / self.duration_s * 100.0
                return round(min(99.0, self._progress + gained), 1)
            return round(self._progress, 1)

    def _times(self) -> tuple[int, int]:
        pct = self.progress()
        printing = round(self.duration_s * pct / 100.0)
        return printing, max(0, self.duration_s - printing)

    def version(self) -> dict[str, Any]:
        return {
            "api": "2.0.0",
            "version": "2.1.2",
            "printer": "1.0.0",
            "text": "PrusaLink",
            "firmware": "6.2.6+8948",
            "capabilities": {"upload-by-put": True},
        }

    def info(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "location": "Workbench",
            "farm_mode": False,
            "nozzle_diameter": 0.4,
            "min_extrusion_temp": 170,
            "serial": "MOCK0000000000000000",
            "hostname": "prusa-mock.local",
            "mmu": False,
            "sd_ready": True,
            "active_camera": False,
        }

    def status(self) -> dict[str, Any]:
        scenario = self.scenario
        hot = scenario in _HOT
        printer: dict[str, Any] = {
            "state": scenario.upper(),
            "temp_nozzle": 214.8 if hot else 26.4,
            "target_nozzle": 215.0 if hot else 0.0,
            "temp_bed": 59.7 if hot else 24.9,
            "target_bed": 60.0 if hot else 0.0,
            "axis_z": 12.4 if scenario in _WITH_JOB else 0.0,
            "flow": 100,
            "speed": 100,
            "fan_hotend": 6800 if hot else 0,
            "fan_print": 5200 if scenario == "printing" else 0,
            "status_connect": {"ok": True, "message": "OK"},
        }
        if scenario not in ("printing",):
            # The real API only reports X and Y while the head is still.
            printer["axis_x"] = 125.0
            printer["axis_y"] = 105.0
        body: dict[str, Any] = {
            "printer": printer,
            "storage": {"path": "/usb/", "name": "usb", "read_only": False},
        }
        if scenario in _WITH_JOB:
            printing, remaining = self._times()
            body["job"] = {
                "id": self.job_id,
                "progress": self.progress(),
                "time_printing": printing,
                "time_remaining": remaining,
            }
        return body

    def job(self) -> dict[str, Any] | None:
        scenario = self.scenario
        if scenario not in _WITH_JOB:
            return None
        printing, remaining = self._times()
        # The Job schema has no ATTENTION state; a printer waiting on its
        # owner still reports the job as PRINTING.
        state = "PRINTING" if scenario == "attention" else scenario.upper()
        short = "TESSER~1.BGC"
        return {
            "id": self.job_id,
            "state": state,
            "progress": self.progress(),
            "time_printing": printing,
            "time_remaining": remaining,
            "inaccurate_estimates": False,
            "file": {
                "name": short,
                "display_name": self.file_name,
                "path": f"/PrusaLink gcodes/{self.file_name}",
                "display_path": f"/PrusaLink gcodes/{self.file_name}",
                "size": 2393142,
                "m_timestamp": 1794268800,
                # Unencoded, space included: this is what a real Raspberry Pi
                # PrusaLink sends, and a client has to cope with it.
                "refs": {
                    "download": f"/api/files/local/PrusaLink gcodes/{self.file_name}/raw",
                    "icon": None,
                    "thumbnail": f"/api/thumbnails/PrusaLink gcodes/{self.file_name}.orig.png",
                },
            },
        }

    def preview(self) -> bytes | None:
        """The model preview, or None when this printer's files have none."""
        if not self.thumbnail:
            return None
        with self._lock:
            if self._png is None:
                self._png = _preview_png()
            return self._png

    # -- authentication ------------------------------------------------

    def challenge(self) -> str:
        return (
            f'Digest realm="{REALM}",qop="auth",algorithm="{self.digest_algorithm}",'
            f'nonce="{self.nonce}"'
        )

    def check(self, method: str, headers: Any) -> int:
        """200 when signed in, 403 for an API key that isn't ours (whatever
        else the request carries), 401 otherwise."""
        key = headers.get("X-Api-Key")
        if key is not None:
            if self.auth in ("both", "apikey") and secrets.compare_digest(
                key, self.api_key
            ):
                return 200
            return 403
        if self.auth in ("both", "digest") and self._digest_ok(
            method, headers.get("Authorization") or ""
        ):
            return 200
        return 401

    def _digest_ok(self, method: str, header: str) -> bool:
        if not header.lower().startswith("digest "):
            return False
        fields = {
            k.lower(): (quoted if quoted else bare)
            for k, quoted, bare in re.findall(
                r'(\w+)=(?:"([^"]*)"|([^\s,]+))', header[7:]
            )
        }
        if fields.get("username") != self.username or fields.get("nonce") != self.nonce:
            return False
        if fields.get("algorithm", self.digest_algorithm) != self.digest_algorithm:
            return False  # compared letter for letter, as the real one does
        algorithm = self.digest_algorithm.upper()
        hasher = hashlib.sha256 if algorithm.startswith("SHA-256") else hashlib.md5

        def h(text: str) -> str:
            return hasher(text.encode("utf-8")).hexdigest()

        ha1 = h(f"{self.username}:{REALM}:{self.password}")
        if algorithm.endswith("-SESS"):
            ha1 = h(f"{ha1}:{fields.get('nonce', '')}:{fields.get('cnonce', '')}")
        ha2 = h(f"{method}:{fields.get('uri', '')}")
        if fields.get("qop"):
            expected = h(
                f"{ha1}:{fields.get('nonce', '')}:{fields.get('nc', '')}:"
                f"{fields.get('cnonce', '')}:{fields['qop']}:{ha2}"
            )
        else:
            expected = h(f"{ha1}:{fields.get('nonce', '')}:{ha2}")
        return secrets.compare_digest(expected, fields.get("response", ""))


class _Handler(BaseHTTPRequestHandler):
    server_version = "PrusaLinkMock/1.0"
    protocol_version = "HTTP/1.1"

    @property
    def printer(self) -> MockPrinter:
        return self.server.printer  # type: ignore[attr-defined,no-any-return]

    def log_message(self, format: str, *args: Any) -> None:
        if getattr(self.server, "quiet", False):
            return
        super().log_message(format, *args)

    def _send(
        self, status: int, body: Any = None, headers: dict[str, str] | None = None
    ) -> None:
        if isinstance(body, bytes):
            payload, content_type = body, "image/png"
        else:
            payload = b"" if body is None else json.dumps(body).encode("utf-8")
            content_type = "application/json"
        self.send_response(status)
        if payload:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def _route(self, method: str) -> None:
        path = self.path.split("?", 1)[0].rstrip("/") or "/"

        # Controls for whoever is driving the mock; not part of PrusaLink.
        if path.startswith("/_mock/scenario"):
            wanted = path.rsplit("/", 1)[-1]
            if wanted in SCENARIOS and method == "POST":
                self.printer.set_scenario(wanted)
            self._send(
                200, {"scenario": self.printer.scenario, "scenarios": list(SCENARIOS)}
            )
            return

        verdict = self.printer.check(method, self.headers)
        if verdict == 403:
            self._send(403, {"title": "Forbidden", "text": "Unknown API key."})
            return
        if verdict == 401:
            self._send(
                401,
                {"title": "Unauthorized", "text": "Sign in to PrusaLink."},
                {"WWW-Authenticate": self.printer.challenge()},
            )
            return

        if method != "GET":
            self._send(
                405, {"title": "Method not allowed", "text": "The mock is read-only."}
            )
        elif path == "/api/version":
            self._send(200, self.printer.version())
        elif path == "/api/v1/info":
            self._send(200, self.printer.info())
        elif path == "/api/v1/status":
            self._send(200, self.printer.status())
        elif path == "/api/v1/job":
            job = self.printer.job()
            self._send(204 if job is None else 200, job)
        elif unquote(path).startswith("/api/thumbnails/PrusaLink gcodes/"):
            png = self.printer.preview() if self.printer.job() else None
            if png is None:
                self._send(
                    404, {"title": "Not found", "text": "This file has no preview."}
                )
            else:
                self._send(200, png)
        else:
            self._send(404, {"title": "Not found", "text": f"No such endpoint: {path}"})

    def do_GET(self) -> None:
        self._route("GET")

    def do_POST(self) -> None:
        self._route("POST")


def make_server(
    printer: MockPrinter | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    quiet: bool = True,
) -> ThreadingHTTPServer:
    """Build a server around ``printer``. ``port=0`` picks a free one;
    read it back from ``server.server_address[1]``."""
    server = ThreadingHTTPServer((host, port), _Handler)
    server.daemon_threads = True
    server.printer = printer or MockPrinter()  # type: ignore[attr-defined]
    server.quiet = quiet  # type: ignore[attr-defined]
    return server


def serve_in_thread(
    printer: MockPrinter | None = None,
) -> tuple[ThreadingHTTPServer, str]:
    """Start a mock on a free loopback port. Returns the server (call
    ``shutdown()`` and ``server_close()`` when done) and its base URL."""
    server = make_server(printer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A stand-in PrusaLink printer.")
    parser.add_argument(
        "--host", default="127.0.0.1", help="bind address (default 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"default {DEFAULT_PORT}"
    )
    parser.add_argument("--scenario", default="printing", choices=SCENARIOS)
    parser.add_argument("--username", default=DEFAULT_USERNAME)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument(
        "--api-key",
        default=None,
        help="a key separate from the password (default: the password)",
    )
    parser.add_argument(
        "--auth",
        default="both",
        choices=("both", "apikey", "digest"),
        help="which sign-in methods to accept (default both)",
    )
    parser.add_argument(
        "--digest-algorithm",
        default="MD5",
        choices=("MD5", "SHA-256"),
        help="default MD5",
    )
    parser.add_argument(
        "--progress", type=float, default=42.0, help="starting percent done"
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=3 * 3600 + 12 * 60,
        help="length of the print in seconds",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="let progress climb while the scenario is printing",
    )
    parser.add_argument("--name", default="Original Prusa MK3.5S", help="printer name")
    parser.add_argument(
        "--file", default=DEFAULT_FILE, help="name of the file being printed"
    )
    parser.add_argument(
        "--no-thumbnail",
        action="store_true",
        help="serve files that have no model preview",
    )
    args = parser.parse_args(argv)

    printer = MockPrinter(
        args.scenario,
        username=args.username,
        password=args.password,
        auth=args.auth,
        progress=args.progress,
        duration_s=args.duration,
        live=args.live,
        name=args.name,
        file_name=args.file,
        digest_algorithm=args.digest_algorithm,
        thumbnail=not args.no_thumbnail,
        api_key=args.api_key,
    )
    server = make_server(printer, host=args.host, port=args.port, quiet=False)
    print(
        f"Mock PrusaLink at http://{args.host}:{server.server_address[1]}  "
        f"scenario={args.scenario}  user={args.username}  password={args.password}  "
        f"auth={args.auth}"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
