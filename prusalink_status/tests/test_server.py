"""server.fetch against the mock printer: every printer state, both ways of
signing in, and the failures an owner can cause."""

from __future__ import annotations

import pytest

from conftest import RUNNING, mock, widget

SETTINGS = {"password_secret": "mock"}


def fetch(url: str, *, options: dict | None = None, **settings) -> dict:
    return widget.fetch(
        options or {}, {"base_url": url, **SETTINGS, **settings}, ctx={}
    )


@pytest.mark.parametrize(
    ("scenario", "tone", "has_job"),
    [
        ("idle", "idle", False),
        ("ready", "idle", False),
        ("busy", "busy", False),
        ("printing", "printing", True),
        ("paused", "paused", True),
        ("attention", "attention", True),
        ("finished", "complete", True),
        ("stopped", "stopped", True),
        ("error", "error", False),
    ],
)
def test_every_printer_state(printer, scenario: str, tone: str, has_job: bool) -> None:
    data = fetch(printer(scenario))
    assert "error" not in data
    assert data["state"]["tone"] == tone
    assert data["state"]["raw"] == scenario.upper()
    assert (data["job"] is not None) is has_job
    assert data["title"] == "Original Prusa MK3.5S"


def test_printing_job_details(printer) -> None:
    data = fetch(printer("printing", progress=42.0, duration_s=10_000))
    job = data["job"]
    # The G-code extension is dropped from the name.
    assert job["name"] == "Tesserae_Frame_Stand_0.2mm_PLA_MK3.5S_3h12m"
    assert job["completion"] == 42.0
    assert job["elapsed"] == 4200
    assert job["remaining"] == 5800
    assert job["eta"] is not None
    assert data["temps"]["tool"] == {"actual": 214.8, "target": 215.0}
    assert data["temps"]["bed"] == {"actual": 59.7, "target": 60.0}
    # Flat copies for the canvas editor's bindings.
    assert data["progress"] == 42.0
    assert data["remaining_min"] == 97
    assert data["nozzle"] == 214.8


def test_paused_print_has_no_finish_time(printer) -> None:
    job = fetch(printer("paused"))["job"]
    assert job["remaining"] is not None
    assert job["eta"] is None


def test_finished_print_reads_100(printer) -> None:
    job = fetch(printer("finished", progress=42.0))["job"]
    assert job["completion"] == 100.0
    assert job["remaining"] == 0


@pytest.mark.parametrize("auth", ["apikey", "digest"])
@pytest.mark.parametrize("algorithm", ["MD5", "SHA-256"])
def test_signs_in_either_way(printer, auth: str, algorithm: str) -> None:
    data = fetch(printer("printing", auth=auth, digest_algorithm=algorithm))
    assert data["state"]["tone"] == "printing"
    assert data["job"]["name"]


def test_custom_username_for_digest(printer) -> None:
    url = printer("idle", auth="digest", username="kayden")
    assert "error" in fetch(url)
    assert fetch(url, username="kayden")["state"]["tone"] == "idle"


def test_wrong_password_is_an_error(printer) -> None:
    data = widget.fetch(
        {}, {"base_url": printer("printing"), "password_secret": "nope"}, ctx={}
    )
    assert "refused the PrusaLink password" in data["error"]


def test_missing_settings_are_errors() -> None:
    assert "address" in widget.fetch({}, {}, ctx={})["error"]
    assert (
        "password" in widget.fetch({}, {"base_url": "http://printer"}, ctx={})["error"]
    )


def test_printer_switched_off_is_offline_not_an_error(printer) -> None:
    url = printer("printing")
    # Free the port, then ask again: nothing is listening.
    server = mock.make_server()
    dead = f"http://127.0.0.1:{server.server_address[1]}"
    server.server_close()
    data = fetch(dead)
    assert "error" not in data
    assert data["state"]["tone"] == "offline"
    assert data["job"] is None
    assert fetch(url)["state"]["tone"] == "printing"


def test_title_option_wins_over_printer_name(printer) -> None:
    assert (
        fetch(printer("idle"), options={"title": "Workbench"})["title"] == "Workbench"
    )


def test_address_without_scheme(printer) -> None:
    url = printer("idle")
    assert fetch(url.removeprefix("http://"))["state"]["tone"] == "idle"


def test_model_preview_is_a_png_data_uri(printer) -> None:
    thumb = fetch(printer("printing"))["job"]["thumbnail"]
    assert thumb.startswith("data:image/png;base64,")


def test_model_preview_over_digest(printer) -> None:
    thumb = fetch(printer("printing", auth="digest"))["job"]["thumbnail"]
    assert thumb.startswith("data:image/png;base64,")


def test_no_preview_when_the_file_has_none(printer) -> None:
    job = fetch(printer("printing", thumbnail=False))["job"]
    assert job["thumbnail"] is None
    assert job["name"]


def test_preview_can_be_turned_off(printer) -> None:
    job = fetch(printer("printing"), options={"show_preview": False})["job"]
    assert job["thumbnail"] is None


def test_preview_is_downloaded_once_per_file(printer, tmp_path) -> None:
    url = printer("printing")
    settings = {"base_url": url, **SETTINGS}
    first = widget.fetch({}, settings, ctx={"data_dir": str(tmp_path)})["job"][
        "thumbnail"
    ]
    assert len(list(tmp_path.glob("thumb_*.bin"))) == 1
    # Turn previews off at the printer: the saved copy still answers.
    RUNNING[url].printer.thumbnail = False
    second = widget.fetch({}, settings, ctx={"data_dir": str(tmp_path)})["job"][
        "thumbnail"
    ]
    assert second == first


def test_preview_ignores_links_off_the_printer() -> None:
    meta = {
        "refs": {"thumbnail": "http://elsewhere.example/thumb.png"},
        "m_timestamp": 1,
    }
    assert widget._thumbnail("http://printer", meta, 1, "maker", "pw", None) is None
    meta["refs"]["thumbnail"] = "//elsewhere.example/thumb.png"
    assert widget._thumbnail("http://printer", meta, 1, "maker", "pw", None) is None


def test_preview_rejects_non_images() -> None:
    assert widget._image_mime(b"qoif\x00\x00") is None
    assert widget._image_mime(b"<html>") is None
    assert widget._image_mime(b"\x89PNG\r\n\x1a\n....") == "image/png"
    assert widget._image_mime(b"\xff\xd8\xff\xe0") == "image/jpeg"


def test_hero_preview_is_passed_through_and_forces_the_picture(printer) -> None:
    data = fetch(
        printer("printing"), options={"hero": "preview", "show_preview": False}
    )
    assert data["hero"] == "preview"
    assert data["job"]["thumbnail"].startswith("data:image/png;base64,")


def test_hero_defaults_to_ring(printer) -> None:
    assert fetch(printer("printing"))["hero"] == "ring"
    assert fetch(printer("printing"), options={"hero": "nonsense"})["hero"] == "ring"
