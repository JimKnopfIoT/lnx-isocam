"""HTTP client for the SonoCam Pocket CRY8024.

Over WiFi the camera exposes a single service: an AndServer HTTP server on
port 9999 (``com.cry.acoustic_pocket``). It publishes file-browser web roots
(/download, /pictures, /recordings, /dcim, /update). The ``/download/..`` root
resolves to the device's shared storage (/sdcard), so a path-traversal base of
``/download/..`` lets us browse and pull the whole storage tree.

Directory listings are plain HTML (`<li><a href="...">name</a></li>`) with no
size/type column, so we distinguish dirs from files (and get sizes) with a
cheap ranged GET in :meth:`peek`.
"""

from __future__ import annotations

import html
import http.client
import json
import os
import re
import urllib.parse
from dataclasses import dataclass

_HREF = re.compile(r'href="([^"]+)"')


@dataclass(frozen=True)
class Node:
    relpath: str   # path relative to storage root, e.g. "workdir/recordtest.wav"
    is_dir: bool
    size: int      # bytes (0 for dirs)


class CameraHTTP:
    STORAGE_BASE = "/download/.."  # path-traversal base -> /sdcard

    def __init__(self, host: str, port: int = 9999, timeout: int = 20) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    # --- low level ---------------------------------------------------------
    def _open(self, method: str, path: str, headers: dict | None = None):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        conn.request(method, path, headers=headers or {})
        return conn

    def _abs(self, relpath: str) -> str:
        rel = relpath.strip("/")
        parts = [urllib.parse.quote(p) for p in rel.split("/") if p]
        return self.STORAGE_BASE + "/" + "/".join(parts) if parts else self.STORAGE_BASE + "/"

    # --- public ------------------------------------------------------------
    def health(self) -> str:
        conn = self._open("GET", "/")
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return body.decode("utf-8", "replace").strip()

    def peek(self, relpath: str) -> tuple[bool, int, str]:
        """Cheap probe: (is_dir, size, content_type) without pulling the file."""
        conn = self._open("GET", self._abs(relpath), {"Range": "bytes=0-1"})
        r = conn.getresponse()
        status = r.status
        ct = r.getheader("Content-Type") or ""
        cl = r.getheader("Content-Length")
        crange = r.getheader("Content-Range")
        r.read(2)
        conn.close()
        # A directory (without trailing slash) answers with a 302 -> /…/ listing.
        if status in (301, 302):
            return True, 0, ct
        is_dir = "text/html" in ct
        size = 0
        if not is_dir:
            if crange and "/" in crange:
                size = int(crange.rsplit("/", 1)[-1])
            elif cl and cl.isdigit():
                size = int(cl)
        return is_dir, size, ct

    def listdir(self, relpath: str = "") -> list[str]:
        path = self._abs(relpath)
        if not path.endswith("/"):
            path += "/"
        conn = self._open("GET", path)
        r = conn.getresponse()
        if r.status in (301, 302):
            loc = r.getheader("Location")
            r.read()
            conn.close()
            conn = self._open("GET", loc)
            r = conn.getresponse()
        body = r.read()
        conn.close()
        names = []
        for href in _HREF.findall(body.decode("utf-8", "replace")):
            name = urllib.parse.unquote(html.unescape(href)).rstrip("/").rsplit("/", 1)[-1]
            if name and name not in (".", ".."):
                names.append(name)
        return names

    def walk(self, root: str = ""):
        """Depth-first yield Node for every entry under root."""
        stack = [root]
        seen: set[str] = set()
        while stack:
            d = stack.pop()
            try:
                names = self.listdir(d)
            except OSError:
                continue
            for name in names:
                rel = f"{d}/{name}".strip("/")
                if rel in seen:
                    continue
                seen.add(rel)
                is_dir, size, _ = self.peek(rel)
                yield Node(rel, is_dir, size)
                if is_dir:
                    stack.append(rel)

    def download(self, relpath: str, localpath: str) -> int:
        os.makedirs(os.path.dirname(localpath) or ".", exist_ok=True)
        conn = self._open("GET", self._abs(relpath))
        r = conn.getresponse()
        total = 0
        with open(localpath, "wb") as fh:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                fh.write(chunk)
                total += len(chunk)
        conn.close()
        return total

    # --- /v1 REST API (control + live data) --------------------------------
    # Auth: POST /v1/login?user=admin&password=crysound -> {data: <token>}.
    # The token is then passed as the `userId` query param to every endpoint.
    USER = "admin"
    PASSWORD = "crysound"

    def login(self, user: str | None = None, password: str | None = None) -> str:
        params = urllib.parse.urlencode(
            {"user": user or self.USER, "password": password or self.PASSWORD}
        )
        conn = self._open("POST", f"/v1/login?{params}")
        r = conn.getresponse()
        body = r.read()
        conn.close()
        data = json.loads(body)
        if not data.get("result"):
            raise PermissionError(data.get("info", "login failed"))
        self.token = data["data"]
        return self.token

    def _api(self, endpoint: str, **params) -> dict:
        """GET a /v1 JSON endpoint, auto-adding the auth token as userId."""
        token = getattr(self, "token", None)
        if token is None:
            token = self.login()
        q = urllib.parse.urlencode({"userId": token, **params})
        conn = self._open("GET", f"{endpoint}?{q}")
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return json.loads(body)

    def device_info(self) -> dict:
        return self._api("/v1/devInfo").get("data", {})

    def running_params(self) -> dict:
        return self._api("/v1/deviceRunningParams").get("data", {})

    def sample_rate(self) -> int:
        return self._api("/v1/sampleRate").get("data", {}).get("sampleRate", 0)

    def max_db(self) -> list[dict]:
        """Live acoustic peaks: [{value: dB, x, y}, ...] on the DOA grid."""
        return self._api("/v1/maxdB").get("data", [])

    def albums(self) -> list[dict]:
        return self._api("/v1/album").get("data", [])

    def camera_image(self) -> bytes:
        """Live optical camera frame as JPEG bytes (/v1/getCameraImage)."""
        token = getattr(self, "token", None) or self.login()
        q = urllib.parse.urlencode({"userId": token})
        conn = self._open("GET", f"/v1/getCameraImage?{q}")
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return body
