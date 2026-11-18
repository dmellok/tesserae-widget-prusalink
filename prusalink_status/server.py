"""prusalink_status, live print monitor for an Original Prusa printer.

Reads the printer's own PrusaLink API over the local network (no cloud
account, no Prusa Connect):

    GET /api/v1/status   state, temperatures and job telemetry
    GET /api/v1/job      the current job's file name (204 when there is none)
    GET /api/v1/info     the printer's name, for the title

and returns the shape client.js paints from:

    {
      "title": str,
      "hero": "ring"|"preview",               # which layout the cell asked for
      "time": "HH:MM",                        # server wall clock at fetch
      "state": {"text": str, "tone": str, "raw": str},
                    # tone: printing | paused | attention | complete |
                    #       stopped | error | busy | idle | offline
      "job": {                                # None when nothing is loaded
        "name": str,
        "completion": float|None,             # 0-100 %
        "elapsed": int|None,                  # seconds printed
        "remaining": int|None,                # seconds left
        "eta": "HH:MM"|None,                  # now + remaining
        "estimate_inaccurate": bool,
        "thumbnail": str|None                 # data: URI of the model preview
      } | None,
      "temps": {"tool": {"actual", "target"}, "bed": {"actual", "target"}},
      "speed": int|None,                      # print speed, %
      "z": float|None,                        # Z height, mm
      # flat copies of the above for the canvas editor's data bindings
      "state_text", "progress", "job_name", "eta", "remaining_min",
      "nozzle", "bed"
    }

Signing in: PrusaLink takes a username and password through HTTP Digest, or
an API key in an ``X-Api-Key`` header. On printers with PrusaLink built in the
key is the password; the Raspberry Pi PrusaLink has a separate key. With a key
set it is tried first, alone. Otherwise a request is sent bare; the printer's
401 carries a Digest challenge, which is answered, and only if that is refused
is the password tried as a key. Digest is done here, not with urllib's
handler, because urllib only knows MD5 and SHA-1.

The model preview is the picture the slicer stores inside the G-code file.
The printer serves it behind the same sign-in, and the panel can't sign in,
so it is fetched here and handed to the client as a ``data:`` URI. It is kept
in the widget's data folder per file, so a print costs one download.

A printer that is switched off is a normal state for a 3D printer, so an
unreachable address renders the card as *offline*. ``{"error": ...}`` is kept
for things the owner has to fix: no address, nothing to sign in with, or a
sign-in the printer refuses.

Standard library only, so the module can be tested without the host.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import http.client
import json
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

HTTP_TIMEOUT_S = 6
USER_AGENT = "tesserae/0.1 (+prusalink_status)"
DEFAULT_USERNAME = "maker"
# Slicer previews are a few tens of kilobytes. Anything much larger is not
# worth carrying in the cell's payload, so it is skipped.
MAX_THUMBNAIL_BYTES = 300_000

# PrusaLink printer state -> (design tone, words on the card).
_STATES: dict[str, tuple[str, str]] = {
    "PRINTING": ("printing", "Printing"),
    "PAUSED": ("paused", "Paused"),
    "ATTENTION": ("attention", "Needs attention"),
    "FINISHED": ("complete", "Finished"),
    "STOPPED": ("stopped", "Stopped"),
    "ERROR": ("error", "Error"),
    "BUSY": ("busy", "Busy"),
    "READY": ("idle", "Ready"),
    "IDLE": ("idle", "Idle"),
}

_FILE_SUFFIX = re.compile(r"\.(bgcode|gcode|gco|g)$", re.IGNORECASE)


class _AuthError(Exception):
    """The printer answered, and refused the credentials."""


class _Unreachable(Exception):
    """Nothing answered at the address: off, asleep, wrong address."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A printer has no reason to redirect, and following one would carry
    the password to whatever host it names."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _f(value: Any) -> float | None:
    """A number, or None for anything that isn't one, so the card shows
    a dash instead of a made-up 0."""
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def _secs(value: Any) -> int | None:
    number = _f(value)
    if number is None or number < 0:
        return None
    return round(number)


def _eta(remaining: int | None) -> str | None:
    """Wall-clock finish time. Local time: the server and the panel share a
    timezone on a household install."""
    if not remaining or remaining <= 0:
        return None
    return (datetime.now() + timedelta(seconds=remaining)).strftime("%H:%M")


def _clean_name(name: Any) -> str:
    """The file name without its G-code extension."""
    return _FILE_SUFFIX.sub("", str(name or "").strip())


def _base_url(raw: Any) -> str:
    """Accept ``192.168.1.50`` as well as a full URL."""
    base = str(raw or "").strip().rstrip("/")
    if base and "://" not in base:
        base = "http://" + base
    return base


# -- HTTP Digest (RFC 7616) ------------------------------------------------


def _digest_challenge(headers: Any) -> dict[str, str] | None:
    """The first Digest challenge in a 401's WWW-Authenticate headers."""
    values = headers.get_all("WWW-Authenticate") if headers is not None else None
    for value in values or []:
        match = re.search(r"(?i)\bdigest\s+(.*)", value)
        if not match:
            continue
        return {
            key.lower(): quoted if quoted else bare
            for key, quoted, bare in re.findall(
                r'(\w+)=(?:"([^"]*)"|([^\s,]+))', match.group(1)
            )
        }
    return None


def _digest_header(
    challenge: dict[str, str], *, method: str, uri: str, username: str, password: str
) -> str | None:
    """Build the Authorization header answering ``challenge``, or None when
    the printer asks for an algorithm this code doesn't have."""
    stated = challenge.get("algorithm") or "MD5"
    algorithm = stated.upper()
    family = algorithm.removesuffix("-SESS")
    if family == "MD5":
        hasher = hashlib.md5
    elif family == "SHA-256":
        hasher = hashlib.sha256
    else:
        return None

    def h(text: str) -> str:
        return hasher(text.encode("utf-8")).hexdigest()

    realm = challenge.get("realm", "")
    nonce = challenge.get("nonce", "")
    cnonce = secrets.token_hex(8)
    nc = "00000001"
    ha1 = h(f"{username}:{realm}:{password}")
    if algorithm.endswith("-SESS"):
        ha1 = h(f"{ha1}:{nonce}:{cnonce}")
    ha2 = h(f"{method}:{uri}")

    offered = [
        q.strip().lower() for q in challenge.get("qop", "").split(",") if q.strip()
    ]
    parts = [
        f'username="{username}"',
        f'realm="{realm}"',
        f'nonce="{nonce}"',
        f'uri="{uri}"',
        # Echoed exactly as the printer wrote it: PrusaLink sends "MD5-sess"
        # and compares the answer letter for letter.
        f"algorithm={stated}",
    ]
    if "auth" in offered:
        response = h(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}")
        parts += ["qop=auth", f"nc={nc}", f'cnonce="{cnonce}"']
    elif offered:
        return None  # only auth-int on offer; nobody ships that for a GET API
    else:
        response = h(f"{ha1}:{nonce}:{ha2}")
    parts.append(f'response="{response}"')
    if challenge.get("opaque"):
        parts.append(f'opaque="{challenge["opaque"]}"')
    return "Digest " + ", ".join(parts)


def _open(url: str, headers: dict[str, str], *, binary: bool) -> Any:
    """One GET. Returns the decoded JSON body, or None for 204 / empty; with
    ``binary`` the raw bytes instead, or None when there are too many."""
    request = urllib.request.Request(url, headers=headers)
    with _OPENER.open(request, timeout=HTTP_TIMEOUT_S) as response:
        if response.status == 204:
            return None
        if binary:
            raw = response.read(MAX_THUMBNAIL_BYTES + 1)
            return raw if 0 < len(raw) <= MAX_THUMBNAIL_BYTES else None
        raw = response.read()
    if not raw.strip():
        return None
    return json.loads(raw.decode("utf-8"))


def _try(url: str, headers: dict[str, str], *, binary: bool) -> tuple[bool, Any, Any]:
    """One attempt. ``(True, body, None)`` on success; ``(False, None,
    challenge)`` when the printer answers 401 or 403, with the Digest
    challenge if it sent one. Anything else is ``_Unreachable``."""
    try:
        return True, _open(url, headers, binary=binary), None
    except urllib.error.HTTPError as err:
        code = err.code
        challenge = _digest_challenge(err.headers) if code == 401 else None
        err.close()
        if code in (401, 403):
            return False, None, challenge
        raise _Unreachable(f"HTTP {code}") from None


def _get(
    base: str,
    path: str,
    username: str,
    password: str,
    *,
    api_key: str = "",
    binary: bool = False,
) -> Any:
    """GET ``base + path`` signed in. Raises ``_AuthError`` when the printer
    refuses the sign-in and ``_Unreachable`` when nothing useful answers.

    A key and a Digest response never travel in the same request: the
    Raspberry Pi PrusaLink answers 403 to any request carrying an
    ``X-Api-Key`` it doesn't recognise, even beside a valid Digest response.
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "image/png, image/jpeg" if binary else "application/json",
        "Accept-Encoding": "identity",
    }
    # File paths come from the printer as written on its disk, spaces and
    # all ("/api/thumbnails/PrusaLink gcodes/..."). Encode what a URL can't
    # carry, leaving anything already encoded alone. The Digest answer signs
    # this same encoded form, since that is what goes on the wire.
    path = urllib.parse.quote(path, safe="/%?&=:@!$'()*+,;~-._")
    url = base + path
    try:
        if api_key:
            ok, body, _ = _try(url, {**headers, "X-Api-Key": api_key}, binary=binary)
            if ok:
                return body
            if not password:
                raise _AuthError
        ok, body, challenge = _try(url, headers, binary=binary)
        if ok:
            return body
        if challenge is not None:
            authorization = _digest_header(
                challenge, method="GET", uri=path, username=username, password=password
            )
            if authorization is not None:
                ok, body, _ = _try(
                    url, {**headers, "Authorization": authorization}, binary=binary
                )
                if ok:
                    return body
        ok, body, _ = _try(url, {**headers, "X-Api-Key": password}, binary=binary)
        if ok:
            return body
        raise _AuthError
    except (_AuthError, _Unreachable):
        raise
    except (
        urllib.error.URLError,
        http.client.HTTPException,
        TimeoutError,
        OSError,
        ValueError,
    ) as err:
        # ValueError covers a malformed URL and a body that isn't JSON:
        # something answered, and it wasn't a PrusaLink printer.
        # HTTPException covers a URL http.client refuses to send and a
        # response it can't parse.
        raise _Unreachable(str(err)) from None


# -- model preview ---------------------------------------------------------


def _image_mime(raw: bytes) -> str | None:
    """PNG or JPEG by their first bytes; anything else (the QOI previews made
    for the printer's own screen, an error page) is not shown."""
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


def _thumbnail(
    base: str,
    file_meta: dict[str, Any],
    job_id: Any,
    username: str,
    password: str,
    data_dir: Any,
    api_key: str = "",
) -> str | None:
    """The current file's preview as a ``data:`` URI, or None. Never raises:
    a missing picture costs the picture, not the card."""
    refs = file_meta.get("refs") if isinstance(file_meta.get("refs"), dict) else {}
    path = str(refs.get("thumbnail") or "").strip()
    # Only a path on the printer itself. A full URL would send the password
    # to whatever host it names.
    if not path.startswith("/") or path.startswith("//"):
        return None

    cache: Path | None = None
    if data_dir:
        identity = f"{base}|{path}|{file_meta.get('m_timestamp')}|{job_id}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
        cache = Path(str(data_dir)) / f"thumb_{digest}.bin"

    raw: bytes | None = None
    if cache is not None:
        with contextlib.suppress(OSError):
            raw = cache.read_bytes()
    if raw is None:
        try:
            raw = _get(base, path, username, password, api_key=api_key, binary=True)
        except Exception:
            # Whatever went wrong, the card is worth more than the picture.
            return None
        if not isinstance(raw, bytes) or _image_mime(raw) is None:
            return None
        if cache is not None:
            with contextlib.suppress(OSError):
                cache.parent.mkdir(parents=True, exist_ok=True)
                # One print at a time: drop the previous file's preview.
                for old in cache.parent.glob("thumb_*.bin"):
                    old.unlink(missing_ok=True)
                cache.write_bytes(raw)

    mime = _image_mime(raw)
    if mime is None:
        return None
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


# -- fetch -----------------------------------------------------------------


def _offline(title: str, now: str, hero: str = "ring") -> dict[str, Any]:
    empty = {"actual": None, "target": None}
    return {
        "title": title,
        "hero": hero,
        "time": now,
        "state": {"text": "Offline", "tone": "offline", "raw": "OFFLINE"},
        "job": None,
        "temps": {"tool": dict(empty), "bed": dict(empty)},
        "speed": None,
        "z": None,
        "state_text": "Offline",
        "progress": None,
        "job_name": "",
        "eta": "",
        "remaining_min": None,
        "nozzle": None,
        "bed": None,
    }


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    base = _base_url(settings.get("base_url"))
    username = str(settings.get("username") or "").strip() or DEFAULT_USERNAME
    # secret:true stores under "<name>_secret"; accept the bare name too so a
    # plaintext override in settings.json still works.
    password = str(
        settings.get("password_secret") or settings.get("password") or ""
    ).strip()
    wanted_title = str(options.get("title") or "").strip()
    hero = "preview" if str(options.get("hero") or "").strip() == "preview" else "ring"
    # Leading with the preview needs the picture whatever the toggle says.
    show_preview = hero == "preview" or options.get("show_preview", True) not in (
        False,
        "false",
        "0",
        0,
        "off",
    )
    now = datetime.now().strftime("%H:%M")

    if not base:
        return {"error": "Set the printer's address in the PrusaLink widget settings."}
    api_key = str(
        settings.get("api_key_secret") or settings.get("api_key") or ""
    ).strip()
    if not password and not api_key:
        return {
            "error": "Set the PrusaLink password or API key in the widget settings."
        }

    try:
        status = _get(base, "/api/v1/status", username, password, api_key=api_key)
    except _AuthError:
        return {
            "error": "The printer refused the PrusaLink sign-in. Check the username, "
            "password or API key in the widget settings against the printer."
        }
    except _Unreachable:
        return _offline(wanted_title or "Prusa", now, hero)

    if not isinstance(status, dict) or not isinstance(status.get("printer"), dict):
        # Something answered 200 at that address, and it isn't PrusaLink.
        return {"error": "That address answered, but not as a PrusaLink printer."}

    printer = status["printer"]
    telemetry = status.get("job") if isinstance(status.get("job"), dict) else {}

    raw_state = str(printer.get("state") or "").upper()
    tone, text = _STATES.get(raw_state, ("idle", raw_state.title() or "Ready"))

    # The file name only comes from /api/v1/job. Ask when the status says a
    # job exists; a failure here costs the name, not the card.
    job_doc: dict[str, Any] = {}
    if telemetry:
        try:
            answer = _get(base, "/api/v1/job", username, password, api_key=api_key)
            if isinstance(answer, dict):
                job_doc = answer
        except (_AuthError, _Unreachable):
            job_doc = {}

    title = wanted_title
    if not title:
        try:
            info = _get(base, "/api/v1/info", username, password, api_key=api_key)
            if isinstance(info, dict):
                title = str(info.get("name") or "").strip()
        except (_AuthError, _Unreachable):
            title = ""
    title = title or "Prusa"

    job: dict[str, Any] | None = None
    if telemetry:
        file_meta = job_doc.get("file") if isinstance(job_doc.get("file"), dict) else {}
        name = _clean_name(file_meta.get("display_name") or file_meta.get("name"))
        completion = _f(telemetry.get("progress"))
        if completion is None:
            completion = _f(job_doc.get("progress"))
        remaining = _secs(telemetry.get("time_remaining"))
        if remaining is None:
            remaining = _secs(job_doc.get("time_remaining"))
        elapsed = _secs(telemetry.get("time_printing"))
        if elapsed is None:
            elapsed = _secs(job_doc.get("time_printing"))
        if tone == "complete":
            completion, remaining = 100.0, 0
        live = tone in ("printing", "paused", "attention")
        job = {
            "name": name or "Print job",
            "completion": completion,
            "elapsed": elapsed,
            "remaining": remaining if live or tone == "complete" else None,
            # A paused printer's finish time moves with every minute it waits.
            "eta": _eta(remaining) if tone == "printing" else None,
            "estimate_inaccurate": bool(job_doc.get("inaccurate_estimates")),
            "thumbnail": _thumbnail(
                base,
                file_meta,
                telemetry.get("id"),
                username,
                password,
                (ctx or {}).get("data_dir"),
                api_key,
            )
            if show_preview
            else None,
        }

    temps = {
        "tool": {
            "actual": _f(printer.get("temp_nozzle")),
            "target": _f(printer.get("target_nozzle")),
        },
        "bed": {
            "actual": _f(printer.get("temp_bed")),
            "target": _f(printer.get("target_bed")),
        },
    }
    speed = _f(printer.get("speed"))
    remaining_now = job["remaining"] if job else None

    return {
        "title": title,
        "hero": hero,
        "time": now,
        "state": {"text": text, "tone": tone, "raw": raw_state},
        "job": job,
        "temps": temps,
        "speed": round(speed) if speed is not None else None,
        "z": _f(printer.get("axis_z")),
        "state_text": text,
        "progress": job["completion"] if job else None,
        "job_name": job["name"] if job else "",
        "eta": (job["eta"] or "") if job else "",
        "remaining_min": round(remaining_now / 60)
        if remaining_now is not None
        else None,
        "nozzle": temps["tool"]["actual"],
        "bed": temps["bed"]["actual"],
    }
