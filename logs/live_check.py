"""Live session + profile sanity check. Prints non-sensitive fields only."""
import json
import urllib.request

BASE = "http://127.0.0.1:8000"


def call(path, method="GET", body=None, token=None):
    headers = {"X-FinSight-Client": "web", "Content-Type": "application/json"}
    if token:
        headers["X-FinSight-Session"] = token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


token = call("/api/system/session")["token"]

status = call("/api/system/status", token=token)
print("system.model =", json.dumps(status["model"], ensure_ascii=False))
print("system.market_data =", json.dumps(status["market_data"], ensure_ascii=False))

profiles = call("/api/models", token=token)
safe = []
for p in profiles:
    safe.append({k: v for k, v in p.items() if k not in {"api_key", "api_key_set"}})
print("model_profiles_count =", len(profiles))
for p in safe:
    print("  profile:", json.dumps(p, ensure_ascii=False))
if profiles:
    print("profile_field_names =", sorted(profiles[0].keys()))

try:
    integrations = call("/api/integrations/profiles", token=token)
    print("finance_profiles_count =", len(integrations))
    for p in integrations:
        print("  finance:", json.dumps({k: v for k, v in p.items() if "key" not in k.lower()}, ensure_ascii=False))
except Exception as exc:  # noqa: BLE001
    print("finance_profiles_error =", type(exc).__name__, str(exc)[:120])

identities = call("/api/data/identities", token=token)
print("confirmed_identities =", json.dumps(
    [{k: i.get(k) for k in ("thscode", "name", "confirmed_at")} for i in identities.get("identities", [])],
    ensure_ascii=False))
