"""Minimaler Microsoft-Graph-Client – ausschließlich lesend.

Jede Anfrage läuft über `_get`, das fest die HTTP-Methode GET verwendet.
Zusammen mit der Berechtigung `Files.Read` ist damit ausgeschlossen, dass
dieses Programm etwas in OneDrive verändert.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from urllib.parse import quote

import httpx

from . import config

DELTA_SELECT = ",".join([
    "id", "name", "file", "folder", "root", "photo", "location", "image", "video",
    "parentReference", "size", "eTag", "webUrl", "deleted", "createdDateTime", "fileSystemInfo",
])


class GraphError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"Graph {status}: {message}")
        self.status = status


class NotLoggedIn(GraphError):
    def __init__(self):
        super().__init__(401, "Nicht angemeldet")


class GraphClient:
    def __init__(
        self,
        token_provider: Callable[[], str | None],
        transport: httpx.BaseTransport | None = None,
        max_retries: int = 5,
    ):
        self._token = token_provider
        self._max_retries = max_retries
        self._http = httpx.Client(
            base_url=config.GRAPH_URL,
            transport=transport,
            follow_redirects=True,  # Thumbnails/Inhalte leiten auf vorab signierte URLs um
            timeout=httpx.Timeout(30.0, read=120.0),
        )

    def close(self) -> None:
        self._http.close()

    # -- Kern: die einzige Stelle, die HTTP-Anfragen stellt -----------------

    def _send(self, url: str, params: dict | None = None, stream: bool = False) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            token = self._token()
            if not token:
                raise NotLoggedIn()
            request = self._http.build_request(
                "GET", url, params=params, headers={"Authorization": f"Bearer {token}"}
            )
            response = self._http.send(request, stream=stream)
            if response.status_code in (429, 500, 502, 503, 504) and attempt < self._max_retries:
                wait = float(response.headers.get("Retry-After", 2 ** attempt))
                response.close()
                time.sleep(min(wait, 60))
                continue
            if response.status_code >= 400:
                if stream:
                    response.read()
                message = _error_message(response)
                response.close()
                if response.status_code == 401:
                    raise NotLoggedIn()
                raise GraphError(response.status_code, message)
            return response
        raise GraphError(503, "Zu viele Wiederholungen")

    def _get(self, url: str, params: dict | None = None) -> dict:
        return self._send(url, params).json()

    # -- Lesende Operationen -------------------------------------------------

    def get_item(self, item_id: str) -> dict:
        return self._get(_item_url(item_id))

    def child_folders(self, item_id: str) -> list[dict]:
        """Unterordner eines Ordners (für die Ordnerauswahl)."""
        url = f"{_item_url(item_id)}/children"
        params = {"$select": "id,name,folder,parentReference", "$top": "200"}
        folders: list[dict] = []
        while url:
            data = self._get(url, params)
            folders += [i for i in data.get("value", []) if "folder" in i]
            url, params = data.get("@odata.nextLink"), None
        return folders

    def delta(self, item_id: str, delta_link: str | None = None) -> Iterator[tuple[list[dict], str | None]]:
        """Liefert Seiten (items, delta_link). delta_link ist nur auf der letzten Seite gesetzt."""
        if delta_link:
            url, params = delta_link, None
        else:
            url, params = f"{_item_url(item_id)}/delta", {"$select": DELTA_SELECT}
        while url:
            data = self._get(url, params)
            next_link = data.get("@odata.nextLink")
            yield data.get("value", []), data.get("@odata.deltaLink")
            url, params = next_link, None

    def thumbnail(self, item_id: str, size: str = "medium") -> bytes:
        if size not in ("small", "medium", "large"):
            raise ValueError(size)
        return self._send(f"{_item_url(item_id)}/thumbnails/0/{size}/content").content

    @contextmanager
    def content(self, item_id: str) -> Iterator[Iterator[bytes]]:
        """Originaldatei als Byte-Stream (nur Download)."""
        response = self._send(f"{_item_url(item_id)}/content", stream=True)
        try:
            yield response.iter_bytes(chunk_size=1024 * 256)
        finally:
            response.close()


def _item_url(item_id: str) -> str:
    if item_id == "root":
        return "/me/drive/root"
    return f"/me/drive/items/{quote(item_id, safe='!')}"


def _error_message(response: httpx.Response) -> str:
    try:
        return response.json()["error"]["message"]
    except Exception:
        return response.text[:200] or response.reason_phrase
