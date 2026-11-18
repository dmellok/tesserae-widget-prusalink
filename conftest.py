"""Test setup for the PrusaLink widget.

The data tests (``test_server.py``) need nothing but this repository: they
run ``server.fetch`` against the mock printer in ``mock/``.

The render tests (``test_render.py``) need a Tesserae checkout to render the
widget inside. Point ``TESSERAE_REPO`` at one, or keep this repository next
to it as ``../../tesserae``; without either they are skipped.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

HERE = Path(__file__).resolve().parent
PLUGIN_DIR = HERE / "prusalink_status"


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


mock = _load("prusalink_mock", HERE / "mock" / "prusalink_mock.py")
widget = _load("prusalink_status_server", PLUGIN_DIR / "server.py")


# Mock servers by base URL, so a test can change a running printer.
RUNNING: dict = {}


def _host_repo() -> Path | None:
    candidates = [
        os.environ.get("TESSERAE_REPO", ""),
        str(HERE.parent.parent / "tesserae"),
    ]
    for raw in candidates:
        if raw and (Path(raw) / "app" / "main.py").is_file():
            return Path(raw).resolve()
    return None


@pytest.fixture
def printer():
    """Start a mock printer; call it with MockPrinter arguments to get its URL."""
    servers = []

    def start(scenario: str = "printing", **kwargs) -> str:
        server, url = mock.serve_in_thread(mock.MockPrinter(scenario, **kwargs))
        servers.append(server)
        RUNNING[url] = server
        return url

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture
def host_app(tmp_path: Path):
    """A Tesserae app with this widget installed the way the catalog would."""
    repo = _host_repo()
    if repo is None:
        pytest.skip("no Tesserae checkout found; set TESSERAE_REPO to run render tests")
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from app.main import create_app

    installed = tmp_path / "marketplace"
    installed.mkdir()
    (installed / "prusalink_status").symlink_to(PLUGIN_DIR, target_is_directory=True)
    return create_app(testing=True, data_root=tmp_path, plugins_dir=repo / "plugins")
