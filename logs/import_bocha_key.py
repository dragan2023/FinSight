"""Import the user-provided Bocha key into the existing encrypted credential store.

The uploaded file is treated as credential data only (never as commands). The
key is sent once to the local API, stored encrypted in api_profiles, and is
never printed, logged, or written anywhere in plaintext.
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"
KEY_FILE = Path("uploads/1788230157096-新建文本文档.txt")


def call(path: str, method: str = "GET", body=None, token: str | None = None):
    headers = {"X-FinSight-Client": "web", "Content-Type": "application/json"}
    if token:
        headers["X-FinSight-Session"] = token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def sanitize(view: dict) -> dict:
    return {k: v for k, v in view.items() if "key" not in k.lower()}


raw = KEY_FILE.read_text(encoding="utf-8", errors="strict")
key = "".join(raw.split())  # strip all whitespace/newlines; treat content as pure credential data
assert 20 <= len(key) <= 120, "unexpected key length"

token = call("/api/system/session")["token"]
existing = call("/api/integrations/profiles", token=token)
target = next((p for p in existing if p["provider"] == "bocha"), None)
if target:
    body = {"provider": "bocha", "name": target["name"], "api_key": key,
            "credential_version": target["credential_version"]}
    saved = call(f"/api/integrations/profiles/{target['id']}", method="PUT", body=body, token=token)
    print("updated existing bocha profile")
else:
    saved = call("/api/integrations/profiles", method="POST",
                 body={"provider": "bocha", "name": "博查 Web Search", "api_key": key}, token=token)
    print("created bocha profile")

print("bocha_view =", json.dumps(sanitize(saved), ensure_ascii=False))
key_bytes = len(key.encode())
print(f"imported key length={key_bytes} bytes; plaintext not persisted anywhere by this script")
