#!/usr/bin/env python3
"""OpenClaw bridge: relays ReRoute dashboard instructions to OpenClaw in a NemoClaw sandbox.

Runs on the NemoClaw host (GCP VM) as a systemd service. Outbound HTTPS only - no open port:
  1. heartbeat + claim   POST {REROUTE_URL}/api/bridge/openclaw/claim   (Bearer REROUTE_MCP_TOKEN)
  2. run one agent turn  nemoclaw <sandbox> agent --session-id reroute-<task> -m "<instruction + task_id>"
     OpenClaw attaches to that task with open_recovery_task(task_id=...) and plans over ReRoute's MCP server
  3. report              POST {REROUTE_URL}/api/bridge/openclaw/tasks/<id>/reply  {text, ok}

The hosted NVIDIA endpoint intermittently answers "Service temporarily overloaded"; a turn that hits it is retried
with a fresh session. Standard library only.

Environment: REROUTE_URL, REROUTE_MCP_TOKEN (or REROUTE_MCP_TOKEN_FILE), NEMOCLAW_SANDBOX, NEMOCLAW_BIN,
BRIDGE_POLL_SECONDS (3), BRIDGE_TURN_TIMEOUT (900), BRIDGE_MAX_ATTEMPTS (4).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
import urllib.error
import urllib.request

log = logging.getLogger("openclaw-bridge")

URL = os.environ.get("REROUTE_URL", "").rstrip("/")
SANDBOX = os.environ.get("NEMOCLAW_SANDBOX", "reroute-claw")
NEMOCLAW = os.environ.get("NEMOCLAW_BIN", os.path.expanduser("~/.local/bin/nemoclaw"))
POLL = float(os.environ.get("BRIDGE_POLL_SECONDS", "3"))
TURN_TIMEOUT = int(os.environ.get("BRIDGE_TURN_TIMEOUT", "900"))
MAX_ATTEMPTS = int(os.environ.get("BRIDGE_MAX_ATTEMPTS", "4"))
MODEL = os.environ.get("NEMOCLAW_MODEL", "nvidia/nemotron-3-super-120b-a12b")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
TRANSIENT = ("temporarily overloaded", "try again in a moment", "rate limit", "429")
# Once OpenClaw proposed a plan the operator's part begins; a failed final answer is not worth another turn.
PROPOSED = {"WAITING_APPROVAL", "EXECUTING", "COMPLETED", "REJECTED"}
PROPOSED_NOTE = "재배정안을 제안했고 운영자 승인을 기다립니다. (OpenClaw의 마지막 답변은 모델 과부하로 받지 못했습니다.)"

PROMPT = """{command}

(ReRoute task_id={task_id}) The operator created this task on the ReRoute dashboard for you.
Call the ReRoute MCP tool open_recovery_task with task_id="{task_id}" first - do not open a new task -
then plan the recovery with the ReRoute tools and finish by proposing the plan for human approval.
Answer the operator briefly in the language of the instruction."""


def _token() -> str:
    path = os.environ.get("REROUTE_MCP_TOKEN_FILE")
    if path:
        with open(path) as f:
            return f.read().strip()
    return os.environ["REROUTE_MCP_TOKEN"]


def api(method: str, path: str, body: dict | None = None) -> tuple[int, dict | None]:
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request(
        f"{URL}{path}",
        data=data if method != "GET" else None,
        method=method,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
            "User-Agent": "openclaw-bridge/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, None


def run_turn(task_id: str, command: str) -> tuple[bool, str]:
    """One OpenClaw turn; retried with a new session when the hosted model is overloaded."""
    prompt = PROMPT.format(command=command, task_id=task_id)
    out = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        session = f"reroute-{task_id}-{attempt}"
        try:
            p = subprocess.run(
                [NEMOCLAW, SANDBOX, "agent", "--session-id", session, "-m", prompt],
                capture_output=True,
                check=False,
                text=True,
                timeout=TURN_TIMEOUT,
            )
            out = ANSI.sub("", (p.stdout or "") + (p.stderr or ""))
            ok = p.returncode == 0
        except subprocess.TimeoutExpired:
            out, ok = f"OpenClaw did not answer within {TURN_TIMEOUT}s", False
        if ok:
            return True, answer(out)
        state = task_state(task_id)
        if state in PROPOSED:
            log.info(
                "task %s: already %s - not retrying the final answer", task_id, state
            )
            return True, PROPOSED_NOTE
        if not any(t in out.lower() for t in TRANSIENT) or attempt == MAX_ATTEMPTS:
            return False, answer(out)
        wait = min(60, 15 * attempt)
        log.warning(
            "task %s: model overloaded (attempt %d/%d), retrying in %ss",
            task_id,
            attempt,
            MAX_ATTEMPTS,
            wait,
        )
        time.sleep(wait)
    return False, answer(out)


def task_state(task_id: str) -> str | None:
    status, task = api("GET", f"/api/agent/tasks/{task_id}")
    return task.get("state") if status == 200 and task else None


def answer(out: str) -> str:
    """Drop NemoClaw's banner lines; keep the agent's reply (or the error)."""
    lines = [
        ln
        for ln in out.splitlines()
        if ln.strip()
        and not ln.startswith(("[gateway]", "[proxy]", "✓ Active gateway"))
    ]
    return "\n".join(lines).strip()[-6000:] or "(no reply)"


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    if not URL:
        raise SystemExit("REROUTE_URL is required")
    log.info("bridge up: %s -> sandbox %s", URL, SANDBOX)
    last_hb = 0.0
    while True:
        try:
            if time.time() - last_hb > 10:
                api(
                    "POST",
                    "/api/bridge/openclaw/heartbeat",
                    {"sandbox": SANDBOX, "model": MODEL},
                )
                last_hb = time.time()
            status, job = api("POST", "/api/bridge/openclaw/claim")
            if status == 200 and job:
                task = job["task"]
                log.info("task %s: %s", task["id"], task["command"])
                ok, text = run_turn(task["id"], task["command"])
                code, _ = api(
                    "POST",
                    f"/api/bridge/openclaw/tasks/{task['id']}/reply",
                    {"text": text, "ok": ok},
                )
                log.info("task %s: ok=%s reply=%s", task["id"], ok, code)
                continue
            if status not in (200, 204):
                log.warning("claim returned HTTP %s", status)
        except Exception:  # keep relaying
            log.exception("bridge iteration failed")
        time.sleep(POLL)


if __name__ == "__main__":
    main()
