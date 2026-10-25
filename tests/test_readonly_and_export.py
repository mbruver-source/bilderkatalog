import ast
import zipfile
from pathlib import Path

import httpx

from bilderkatalog import config, export
from bilderkatalog.graph_client import GraphClient

PACKAGE = Path(__file__).parent.parent / "bilderkatalog"


def test_only_read_permission_requested():
    assert config.SCOPES == ["Files.Read"]


def test_graph_client_uses_only_get(graph):
    graph.route("/me/drive/root", {"id": "ROOT", "root": {}})
    graph.route("/me/drive/items/X/thumbnails/0/medium/content", b"jpeg")
    graph.route("/me/drive/items/X/content", b"original")
    client = graph.client()
    client.get_item("root")
    client.thumbnail("X")
    with client.content("X") as chunks:
        assert b"".join(chunks) == b"original"
    assert graph.requests and {r.method for r in graph.requests} == {"GET"}


def test_no_write_http_methods_in_source():
    """Statische Prüfung: kein Modul ruft schreibende HTTP-Methoden auf."""
    forbidden = {"post", "put", "patch", "delete"}
    for source in PACKAGE.glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                owner = ast.unparse(node.func.value)
                if node.func.attr in forbidden and ("http" in owner or "client" in owner):
                    raise AssertionError(f"{source.name}: {ast.unparse(node)}")
            if isinstance(node, ast.Constant) and node.value in ("POST", "PUT", "PATCH", "DELETE"):
                assert source.name == "api.py", f"{source.name} enthält HTTP-Methode {node.value}"


def test_retry_on_throttling(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, json={"id": "ROOT"})

    client = GraphClient(lambda: "t", transport=httpx.MockTransport(handler))
    assert client.get_item("root")["id"] == "ROOT"
    assert len(calls) == 2


def test_export_zip(conn, graph, tmp_path):
    conn.executemany(
        "INSERT INTO photo (id, name, parent_path, taken_at, year, month) VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("A", "bild.jpg", "/Reise/Tag1", "2021-07-14T09:30:00", 2021, 7),
            ("B", "bild.jpg", "/Reise/Tag2", "2021-08-01T10:00:00", 2021, 8),
            ("C", "fehlt.jpg", "/Reise", None, None, None),
        ],
    )
    conn.commit()
    graph.route("/me/drive/items/A/content", b"AAA")
    graph.route("/me/drive/items/B/content", b"BBB")
    client = graph.client()

    flat = export.export_zip(conn, client, ["A", "B", "C"], tmp_path, "Reise: 2021?", "flat")
    with zipfile.ZipFile(flat["path"]) as zf:
        assert sorted(zf.namelist()) == ["bild (2).jpg", "bild.jpg"]
        assert zf.read("bild.jpg") == b"AAA"
        assert zf.getinfo("bild.jpg").date_time == (2021, 7, 14, 9, 30, 0)
    assert flat["written"] == 2 and len(flat["errors"]) == 1
    assert Path(flat["path"]).name.startswith("Reise_ 2021_")

    by_month = export.export_zip(conn, client, ["A", "B"], tmp_path, "x", "year_month")
    with zipfile.ZipFile(by_month["path"]) as zf:
        assert sorted(zf.namelist()) == ["2021/07/bild.jpg", "2021/08/bild.jpg"]

    by_folder = export.export_zip(conn, client, ["A"], tmp_path, "y", "folder")
    with zipfile.ZipFile(by_folder["path"]) as zf:
        assert zf.namelist() == ["Reise/Tag1/bild.jpg"]
    assert not list(tmp_path.glob("*.part"))
