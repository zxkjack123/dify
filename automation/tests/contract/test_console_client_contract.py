import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from automation.clients.dify_console_client import DifyConsoleClient

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "console"


def load_fixture(name: str) -> Dict[str, Any]:
    path = FIXTURE_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def make_response(
    payload: Dict[str, Any],
    status_code: int = 200
) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


@pytest.fixture(autouse=True)
def config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIFY_CONSOLE_URL", "https://console.dify.test")
    monkeypatch.setenv("DIFY_EMAIL", "contract@example.com")
    monkeypatch.setenv("DIFY_PASSWORD", "contract-pass")


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    session = MagicMock()
    monkeypatch.setattr(
        "automation.clients.dify_console_client.requests.Session",
        lambda: session
    )
    return session


def test_get_apps_matches_fixture(session: MagicMock) -> None:
    login_payload = load_fixture("login_success")
    apps_payload = load_fixture("apps_list")

    session.post.return_value = make_response(login_payload)
    session.request.return_value = make_response(apps_payload)

    client = DifyConsoleClient()
    data = client.get_apps()

    assert data == apps_payload
    session.post.assert_called_once()
    session.request.assert_called_once()
    method, url = session.request.call_args[0][:2]
    assert method == "GET"
    assert url == "https://console.dify.test/console/api/apps"


def test_import_app_payload_and_response(session: MagicMock) -> None:
    login_payload = load_fixture("login_success")
    import_payload = load_fixture("import_success")

    session.post.return_value = make_response(login_payload)
    session.request.return_value = make_response(import_payload)

    client = DifyConsoleClient()
    result = client.import_app(
        mode="yaml-content",
        yaml_content="kind: app",
        app_id="app-123"
    )

    assert result == import_payload
    session.request.assert_called_once()
    args, kwargs = session.request.call_args
    assert args[0] == "POST"
    assert args[1] == "https://console.dify.test/console/api/apps/imports"
    assert kwargs["json"] == {
        "mode": "yaml-content",
        "yaml_content": "kind: app",
        "app_id": "app-123",
    }


def test_export_app_returns_yaml_string(session: MagicMock) -> None:
    login_payload = load_fixture("login_success")
    export_payload = load_fixture("export_app")

    session.post.return_value = make_response(login_payload)
    session.request.return_value = make_response(export_payload)

    client = DifyConsoleClient()
    result = client.export_app("app-999", include_secret=True)

    assert result == export_payload["data"]
    session.request.assert_called_once()
    args, kwargs = session.request.call_args
    assert args[0] == "GET"
    assert args[1] == (
        "https://console.dify.test/console/api/apps/app-999/export"
    )
    assert kwargs["params"] == {"include_secret": "true"}

