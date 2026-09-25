"""Publish events to the running backend through POST /debug/publish.

Needs DEBUG_ENDPOINTS=1 in backend-ai/.env and the backend running.

Examples:
    python scripts/publish_state.py thinking
    python scripts/publish_state.py --cycle
    python scripts/publish_state.py --pulse 8
    python scripts/publish_state.py --type error --payload '{"severity":"warn","source":"test","message":"hello"}'
"""

import argparse
import http.client
import json
import math
import os
import pathlib
import sys
import time

PHASES = ["idle", "listening", "thinking", "acting", "speaking", "error"]
ENV_PATH = pathlib.Path(__file__).resolve().parent.parent / "backend-ai" / ".env"


def load_token() -> str:
    token = os.environ.get("ULTRON_SESSION_TOKEN", "")
    if token:
        return token
    try:
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and key.strip() == "SESSION_TOKEN":
            return value.strip().strip("\"'")
    return ""


def fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def post(conn: http.client.HTTPConnection, token: str, type_: str, payload: dict, quiet: bool = False) -> None:
    body = json.dumps({"type": type_, "payload": payload})
    try:
        conn.request(
            "POST",
            "/debug/publish",
            body=body,
            headers={"Content-Type": "application/json", "X-Ultron-Token": token},
        )
        resp = conn.getresponse()
        data = resp.read()
    except ConnectionRefusedError:
        fail("backend not running (python orchestrator.py in backend-ai)")
    except OSError as e:
        fail(f"connection failed: {e}")

    if resp.status == 200:
        if not quiet:
            print(f"ok: {type_}")
        return
    if resp.status == 401:
        fail("401 unauthorized: token mismatch with SESSION_TOKEN")
    if resp.status == 404:
        fail("404 not found: set DEBUG_ENDPOINTS=1 in backend-ai/.env and restart the backend")
    if resp.status == 422:
        try:
            details = json.loads(data).get("details", [])
        except ValueError:
            details = [data.decode("utf-8", "replace")]
        fail("422 schema violation: " + "; ".join(str(d) for d in details))
    fail(f"HTTP {resp.status}: {data.decode('utf-8', 'replace')[:200]}")


def run_cycle(conn: http.client.HTTPConnection, token: str) -> None:
    for phase in PHASES[1:]:
        post(conn, token, "state", {"phase": phase})
        time.sleep(3)
    post(conn, token, "state", {"phase": "idle"})


def run_pulse(conn: http.client.HTTPConnection, token: str, seconds: float) -> None:
    post(conn, token, "state", {"phase": "speaking"})
    start = time.monotonic()
    next_tick = start
    try:
        while (t := time.monotonic() - start) < seconds:
            post(conn, token, "audio_level", {"rms": 0.5 + 0.5 * math.sin(t * 6)}, quiet=True)
            next_tick += 1 / 30
            time.sleep(max(0.0, next_tick - time.monotonic()))
    finally:
        post(conn, token, "audio_level", {"rms": 0}, quiet=True)  # don't leave the TTS level stuck
        post(conn, token, "state", {"phase": "idle"})


def main() -> None:
    p = argparse.ArgumentParser(description="Publish a server-to-client event via /debug/publish.")
    p.add_argument("phase", nargs="?", choices=PHASES, help="publish a state event with this phase")
    p.add_argument("--type", dest="type_", help="event type to publish (use with --payload)")
    p.add_argument("--payload", default="{}", help="JSON object payload for --type")
    p.add_argument("--cycle", action="store_true", help="each phase for 3 s, then back to idle")
    p.add_argument("--pulse", nargs="?", const=5.0, type=float, metavar="SECONDS",
                   help="speaking + audio_level sine wave at ~30 Hz (default 5 s), then idle")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args()

    modes = [args.phase is not None, args.type_ is not None, args.cycle, args.pulse is not None]
    if sum(modes) != 1:
        p.error("choose exactly one of: phase, --type, --cycle, --pulse")

    token = load_token()
    if not token:
        fail("no token: set ULTRON_SESSION_TOKEN or SESSION_TOKEN in backend-ai/.env")

    conn = http.client.HTTPConnection(args.host, args.port, timeout=5)
    try:
        if args.phase is not None:
            post(conn, token, "state", {"phase": args.phase})
        elif args.type_ is not None:
            try:
                payload = json.loads(args.payload)
            except ValueError as e:
                fail(f"--payload is not valid JSON: {e}")
            if not isinstance(payload, dict):
                fail("--payload must be a JSON object")
            post(conn, token, args.type_, payload)
        elif args.cycle:
            run_cycle(conn, token)
        else:
            run_pulse(conn, token, args.pulse)
    except KeyboardInterrupt:
        sys.exit(130)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
