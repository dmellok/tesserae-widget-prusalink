"""The widget rendered inside Tesserae, fed by the mock printer. Skipped when
no Tesserae checkout is available (see conftest.py)."""

from __future__ import annotations

import pytest


def _configure(app, url: str) -> None:
    with app.app_context():
        app.config["SETTINGS_STORE"].update_section(
            "plugins",
            {"prusalink_status": {"base_url": url, "password_secret": "mock"}},
        )


@pytest.mark.parametrize("size", ["xs", "sm", "md", "lg"])
def test_renders_a_print_at_every_size(host_app, printer, size: str) -> None:
    _configure(host_app, printer("printing"))
    resp = host_app.test_client().get(
        f"/_test/render?plugin=prusalink_status&size={size}"
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="prusalink_status"' in body
    # The file name travels in the cell's data payload; if the fetch had
    # failed or the cell had errored it would be missing.
    assert "Tesserae_Frame_Stand" in body


def test_renders_without_settings(host_app) -> None:
    resp = host_app.test_client().get("/_test/render?plugin=prusalink_status&size=md")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="prusalink_status"' in body
    assert "Set the printer" in body


@pytest.mark.parametrize("size", ["xs", "sm", "md", "lg"])
def test_renders_with_the_model_preview_leading(host_app, printer, size: str) -> None:
    _configure(host_app, printer("printing"))
    resp = host_app.test_client().get(
        f"/_test/render?plugin=prusalink_status&size={size}&opts=%7B%22hero%22%3A%22preview%22%7D"
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="prusalink_status"' in body
    assert "data:image/png;base64," in body
