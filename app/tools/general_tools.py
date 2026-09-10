from __future__ import annotations

import ipaddress
import json
import socket
from typing import Any
from urllib.parse import urlparse

import httpx


class GeneralTools:
    def __init__(self, max_output_chars: int = 20_000):
        self.max_output_chars = max_output_chars

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        if name != "http_get":
            raise ValueError(f"Unknown general tool: {name}")
        return json.dumps({"ok": True, "result": self.http_get(**arguments)}, ensure_ascii=False)

    def http_get(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Only absolute HTTP(S) URLs are allowed")
        self._assert_public_host(parsed.hostname)
        with httpx.Client(timeout=15.0, follow_redirects=False) as client:
            response = client.get(url, headers={"User-Agent": "MyAgent/0.1"})
        content_type = response.headers.get("content-type", "")
        if not any(kind in content_type for kind in ("text/", "json", "xml")):
            raise ValueError("Only text, JSON, and XML responses are supported")
        body = response.text
        if len(body) > self.max_output_chars:
            body = body[: self.max_output_chars] + "\n... output truncated"
        return f"HTTP {response.status_code}\n{body}"

    @staticmethod
    def _assert_public_host(hostname: str) -> None:
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(hostname, None)}
        except socket.gaierror as exc:
            raise ValueError("Host could not be resolved") from exc
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise PermissionError("Private, local, reserved, and link-local addresses are blocked")


GENERAL_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "http_get",
        "description": "Fetch a public HTTP(S) text or JSON resource. Local and private network addresses are blocked.",
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
            "additionalProperties": False,
        },
        "strict": True,
    }
]

