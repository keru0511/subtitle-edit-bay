from __future__ import annotations

import json
import os
import sys
import time


def _send(payload: dict[str, object], *, partial: bool = False) -> None:
    encoded = json.dumps(payload, ensure_ascii=False) + "\n"
    if partial:
        midpoint = max(1, len(encoded) // 2)
        sys.stdout.write(encoded[:midpoint])
        sys.stdout.flush()
        time.sleep(0.01)
        sys.stdout.write(encoded[midpoint:])
    else:
        sys.stdout.write(encoded)
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = request.get("method")
        request_id = request.get("id")
        if method == "initialize":
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "1",
                        "receivedCapabilities": request.get("params", {}).get("capabilities"),
                    },
                },
                partial=True,
            )
        elif method == "initialized":
            continue
        elif method == "account/read":
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "authenticated": os.environ.get("FAKE_CODEX_AUTHENTICATED") == "1"
                    },
                }
            )
        elif method == "account/login/start":
            _send({"jsonrpc": "2.0", "id": request_id, "result": {"loginId": "login-1", "url": "https://example.invalid/login", "receivedType": request.get("params", {}).get("type")}})
        elif method == "account/login/cancel":
            _send({"jsonrpc": "2.0", "id": request_id, "result": {"cancelled": True}})
        elif method == "account/logout":
            _send({"jsonrpc": "2.0", "id": request_id, "result": {}})
        elif method == "model/list":
            _send({"jsonrpc": "2.0", "id": request_id, "result": {"data": [{"id": "gpt-test", "displayName": "GPT Test", "isDefault": True}]}})
        elif method == "mcpServerStatus/list":
            _send({"jsonrpc": "2.0", "id": request_id, "result": {"data": [], "nextCursor": None}})
        elif method == "thread/start":
            _send({"jsonrpc": "2.0", "id": request_id, "result": {"threadId": "thread-1"}})
        elif method == "thread/resume":
            _send({"jsonrpc": "2.0", "id": request_id, "result": {"threadId": request.get("params", {}).get("threadId")}})
        elif method == "turn/start":
            params = request.get("params", {})
            output_schema = params.get("outputSchema", {})
            required = output_schema.get("required", []) if isinstance(output_schema, dict) else []
            if "schema_version" in required:
                context = {}
                for item in params.get("input", []):
                    if not isinstance(item, dict) or item.get("type") != "text":
                        continue
                    text = str(item.get("text", ""))
                    if text.startswith("参照コンテキスト(JSON):\n"):
                        try:
                            context = json.loads(text.split("\n", 1)[1])
                        except (IndexError, json.JSONDecodeError):
                            context = {}
                        break
                target = str(context.get("target", "normal"))
                operation = (
                    {
                        "id": "fake-add-cut",
                        "type": "add_cut",
                        "source_start": 1.0,
                        "source_end": 2.0,
                        "reason": "fake integration proposal",
                    }
                    if target == "normal"
                    else {
                        "id": "fake-short-target",
                        "type": "set_short_duration_target",
                        "target_seconds": 1.0,
                        "reason": "fake integration proposal",
                    }
                )
                structured_output = {
                    "schema_version": 1,
                    "summary": "fake timeline proposal",
                    "target": target,
                    "operations": [operation],
                    "warnings": [],
                    "base_revision": int(context.get("project_revision", 0)),
                    "base_state_revision": str(context.get("state_revision", "sha256:fake")),
                }
            else:
                structured_output = {"answer": "ok"}
            sys.stdout.write("not-json\n")
            sys.stdout.flush()
            _send({"jsonrpc": "2.0", "method": "turn/started", "params": {"turnId": "turn-1"}})
            _send({"jsonrpc": "2.0", "method": "item/agentMessage/delta", "params": {"delta": "提案"}})
            _send(
                {
                    "jsonrpc": "2.0",
                    "method": "item/completed",
                    "params": {
                        "turnId": "turn-1",
                        "item": {
                            "type": "agentMessage",
                            "text": json.dumps(structured_output),
                        },
                    },
                }
            )
            _send(
                {
                    "jsonrpc": "2.0",
                    "method": "turn/completed",
                    "params": {"turn": {"id": "turn-1", "status": "completed"}},
                }
            )
            _send({"jsonrpc": "2.0", "id": request_id, "result": {"turnId": "turn-1", "status": "completed", "receivedInput": params.get("input"), "receivedModel": params.get("model")}})
        elif method == "turn/interrupt":
            _send({"jsonrpc": "2.0", "id": request_id, "result": {"interrupted": True}})
        elif method == "test/timeout":
            continue
        elif method == "test/exit":
            return
        elif method == "test/approval":
            _send({"jsonrpc": "2.0", "id": 900, "method": "command/approval/request", "params": {"command": "del all"}})
            _send({"jsonrpc": "2.0", "id": request_id, "result": {"approvalRequestSent": True}})
        else:
            _send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "method not found"}})


if __name__ == "__main__":
    main()
