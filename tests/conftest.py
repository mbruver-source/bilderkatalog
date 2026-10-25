"""Gemeinsame Test-Hilfen. Alle Daten sind erfunden."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from bilderkatalog import db
from bilderkatalog.graph_client import GraphClient

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Tests dürfen nie das echte Datenverzeichnis berühren."""
    monkeypatch.setenv("BILDERKATALOG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("BILDERKATALOG_CLIENT_ID", raising=False)


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "test.db")
    db.init(connection)
    yield connection
    connection.close()


class FakeGraph:
    """Simuliert die benötigten Graph-Endpunkte und protokolliert alle Anfragen."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.routes: dict[str, object] = {}

    def route(self, path: str, response) -> None:
        self.routes[path] = response

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = request.url.path.removeprefix("/v1.0")
        if request.url.params.get("token"):
            key += "?token=" + request.url.params["token"]
        response = self.routes.get(key)
        if response is None:
            return httpx.Response(404, json={"error": {"message": f"keine Route für {key}"}})
        if callable(response):
            return response(request)
        if isinstance(response, bytes):
            return httpx.Response(200, content=response)
        return httpx.Response(200, json=response)

    def client(self) -> GraphClient:
        return GraphClient(lambda: "test-token", transport=httpx.MockTransport(self.handler), max_retries=1)


@pytest.fixture
def graph():
    return FakeGraph()


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))
