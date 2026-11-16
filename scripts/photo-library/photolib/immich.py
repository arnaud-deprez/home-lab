"""Minimal Immich REST client (standard library only). The API key travels only in the x-api-key header."""
import json
import urllib.error
import urllib.parse
import urllib.request


class ImmichClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 60.0):
        self.base = base_url.rstrip("/") + "/api"
        self._key = api_key
        self.timeout = timeout

    def _call(self, method: str, path: str, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("x-api-key", self._key)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"{method} {path} failed: HTTP {e.code} {msg}") from None
        except urllib.error.URLError as e:
            raise RuntimeError(f"{method} {path} failed: {e.reason}") from None
        return json.loads(raw) if raw.strip() else None

    def get_duplicates(self) -> list:
        return self._call("GET", "/duplicates")

    def albums_of(self, asset_id: str) -> list:
        albums = self._call("GET", "/albums?assetId=" + urllib.parse.quote(asset_id, safe=""))
        return [a["id"] for a in albums]

    def add_to_albums(self, album_ids: list, asset_ids: list) -> None:
        self._call("PUT", "/albums/assets", {"albumIds": album_ids, "assetIds": asset_ids})

    def set_favorite(self, asset_ids: list) -> None:
        self._call("PUT", "/assets", {"ids": asset_ids, "isFavorite": True})

    def trash(self, asset_ids: list) -> None:
        self._call("DELETE", "/assets", {"ids": asset_ids, "force": False})

    def restore(self, asset_ids: list) -> None:
        self._call("POST", "/trash/restore/assets", {"ids": asset_ids})
