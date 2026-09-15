"""Small subprocess fake for Gemini CLI's newline-delimited ACP protocol."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Any


WRITE_LOCK = threading.Lock()
CANCEL_EVENT = threading.Event()
PERMISSION_DENIED_EVENT = threading.Event()
SESSION_COUNTER = 0


def write_message(message: dict[str, Any]) -> None:
    with WRITE_LOCK:
        sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()


def write_update(session_id: str, text: str) -> None:
    write_message(
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": session_id,
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": text},
                },
            },
        }
    )


def prompt_worker(request_id: object, session_id: str) -> None:
    if os.environ.get("FAKE_ACP_MALFORMED") == "1":
        sys.stdout.write("this is not JSON\n")
        sys.stdout.flush()
    write_update(session_id, "hello ")
    if os.environ.get("FAKE_ACP_PERMISSION") == "1":
        write_message(
            {
                "jsonrpc": "2.0",
                "id": 9001,
                "method": "session/request_permission",
                "params": {
                    "sessionId": session_id,
                    "toolCall": {"title": "run shell"},
                },
            }
        )
        if os.environ.get("FAKE_ACP_PERMISSION_REQUIRE_DENY") == "1":
            PERMISSION_DENIED_EVENT.wait(timeout=2)
    if os.environ.get("FAKE_ACP_CANCEL") == "1":
        CANCEL_EVENT.wait(timeout=5)
        stop_reason = "cancelled"
    else:
        time.sleep(0.01)
        write_update(session_id, "world")
        stop_reason = "end_turn"
    write_message(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"stopReason": stop_reason},
        }
    )


def handle(request: dict[str, Any]) -> None:
    global SESSION_COUNTER
    request_id = request.get("id")
    method = request.get("method")
    params = request.get("params")
    if not isinstance(params, dict):
        params = {}

    if method == "initialize":
        auth_state = os.environ.get("FAKE_ACP_AUTH_STATE", "")
        protocol_version = int(os.environ.get("FAKE_ACP_PROTOCOL_VERSION", "1"))
        auth_methods = [] if os.environ.get("FAKE_ACP_NO_AUTH_METHODS") == "1" else [
            {"id": "oauth-personal", "name": "Log in with Google"},
        ]
        result: dict[str, Any] = {
            "protocolVersion": protocol_version,
            "agentInfo": {"name": "fake-gemini", "version": "0.59.0"},
            "agentCapabilities": {
                "loadSession": True,
                "promptCapabilities": {"image": False},
            },
            "authMethods": auth_methods,
        }
        if auth_state:
            result["authState"] = auth_state
        write_message({"jsonrpc": "2.0", "id": request_id, "result": result})
        return
    if request_id == 9001 and isinstance(request.get("result"), dict):
        outcome = request["result"].get("outcome")
        if isinstance(outcome, dict) and outcome.get("outcome") == "cancelled":
            PERMISSION_DENIED_EVENT.set()
        return
    if method == "authenticate":
        if os.environ.get("FAKE_ACP_AUTH_ERROR") == "1":
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32000, "message": "Authentication required"},
                }
            )
        else:
            write_message({"jsonrpc": "2.0", "id": request_id, "result": {}})
        return
    if method in {"session/new", "session/load", "newSession", "loadSession"}:
        SESSION_COUNTER += 1
        session_id = str(params.get("sessionId") or f"session-{SESSION_COUNTER}")
        models = os.environ.get("FAKE_ACP_MODELS", "")
        result = {"sessionId": session_id}
        if models:
            result["models"] = {
                "availableModels": json.loads(models),
                "currentModelId": json.loads(models)[0].get("modelId", ""),
            }
        if os.environ.get("FAKE_ACP_AUTH_ERROR") == "1":
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32000, "message": "Authentication required"},
                }
            )
        else:
            write_message({"jsonrpc": "2.0", "id": request_id, "result": result})
        return
    if method == "unstable_setSessionModel":
        write_message({"jsonrpc": "2.0", "id": request_id, "result": {}})
        return
    if method in {"session/prompt", "prompt"}:
        if os.environ.get("FAKE_ACP_EXIT_ON_PROMPT") == "1":
            os._exit(0)
        threading.Thread(
            target=prompt_worker,
            args=(request_id, str(params.get("sessionId") or "")),
            daemon=True,
        ).start()
        return
    if method in {"session/cancel", "cancel"}:
        CANCEL_EVENT.set()
        return
    if request_id is not None:
        write_message(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "method not found"},
            }
        )


def main() -> int:
    for raw_line in sys.stdin:
        try:
            request = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if isinstance(request, dict):
            handle(request)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
