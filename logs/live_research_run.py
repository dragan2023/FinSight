"""One-shot live research run: create task, poll to terminal, dump sanitized result.

User-authorized single paid LLM task (stage03 live verification). Writes:
- logs/live_research_progress.log  (progress lines)
- logs/live_research_result.json   (final task + report + raw_sections + events)
No API keys or session tokens are printed or stored here.
"""
from __future__ import annotations

import json
import time
import urllib.request
from uuid import uuid4

BASE = "http://127.0.0.1:8000"
PROGRESS = "logs/live_research_progress.log"
RESULT = "logs/live_research_result.json"
DEADLINE_S = 25 * 60
TERMINAL = {"completed", "interrupted", "cancelled", "failed"}


def log(line: str) -> None:
    with open(PROGRESS, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def call(path: str, method: str = "GET", body=None, token: str | None = None):
    headers = {"X-FinSight-Client": "web", "Content-Type": "application/json"}
    if token:
        headers["X-FinSight-Session"] = token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def main() -> None:
    open(PROGRESS, "w", encoding="utf-8").close()
    token = call("/api/system/session")["token"]

    payload = {
        "question": "贵州茅台（600519）当前盈利质量如何？当前估值隐含了怎样的市场预期？",
        "thscode": "600519.SH",
        "client_request_id": uuid4().hex,
    }
    created = call("/api/research/tasks", method="POST", body=payload, token=token)
    task = created["task"]
    task_id = task["id"]
    log(f"created status={task['status']} replay={created.get('replayed', False)} id={task_id}")

    deadline = time.monotonic() + DEADLINE_S
    last_count = -1
    while time.monotonic() < deadline:
        time.sleep(6)
        detail = call(f"/api/research/tasks/{task_id}", token=token)
        t = detail["task"]
        events = detail.get("events", [])
        usage = t["usage"]
        log(
            f"poll status={t['status']} llm={usage['llm_requests']} fetch={usage['fetches']} "
            f"tools={usage['tool_rounds']} reasoning_chars={usage['reasoning_chars']} events={len(events)}"
        )
        for ev in events[last_count + 1:]:
            brief = {k: v for k, v in ev.get("payload", {}).items()
                     if k in {"phase", "detail", "id", "status", "integrity", "code", "chars", "seq"}}
            log(f"  event seq={ev.get('seq')} type={ev.get('type')} {json.dumps(brief, ensure_ascii=False)[:200]}")
        last_count = len(events)
        if t["status"] in TERMINAL:
            final = detail
            break
    else:
        log("TIMEOUT waiting for terminal state")
        final = call(f"/api/research/tasks/{task_id}", token=token)

    t = final["task"]
    log(f"terminal status={t['status']} report_id={t.get('report_id')} error={t.get('error')}")

    result = {"task": final["task"], "events": final.get("events", [])}
    if t.get("report_id"):
        report = call(f"/api/research/reports/{t['report_id']}", token=token)
        result["report"] = report["report"]
        result["raw_sections"] = report.get("raw_sections", [])
    with open(RESULT, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
    log("DONE")


if __name__ == "__main__":
    main()
