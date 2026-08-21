from __future__ import annotations

import json
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
            _send({"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "1"}}, partial=True)
        elif method == "initialized":
            continue
        elif method == "account/read":
            _send({"jsonrpc": "2.0", "id": request_id, "result": {"authenticated": False}})
        elif method == "account/login/start":
            _send({"jsonrpc": "2.0", "id": request_id, "result": {"loginId": "login-1", "url": "https://example.invalid/login", "receivedType": request.get("params", {}).get("type")}})
        elif method == "account/login/cancel":
            _send({"jsonrpc": "2.0", "id": request_id, "result": {"cancelled": True}})
        elif method == "account/logout":
            _send({"jsonrpc": "2.0", "id": request_id, "result": {}})
        elif method == "model/list":
            _send({"jsonrpc": "2.0", "id": request_id, "result": {"data": [{"id": "gpt-test", "displayName": "GPT Test", "isDefault": True}]}})
        elif method == "thread/start":
            _send({"jsonrpc": "2.0", "id": request_id, "result": {"threadId": "thread-1"}})
        elif method == "thread/resume":
            _send({"jsonrpc": "2.0", "id": request_id, "result": {"threadId": request.get("params", {}).get("threadId")}})
        elif method == "turn/start":
            sys.stdout.write("not-json\n")
            sys.stdout.flush()
            _send({"jsonrpc": "2.0", "method": "turn/started", "params": {"turnId": "turn-1"}})
            _send({"jsonrpc": "2.0", "method": "item/agentMessage/delta", "params": {"delta": "提案"}})
            _send({"jsonrpc": "2.0", "id": request_id, "result": {"turnId": "turn-1", "status": "completed", "receivedInput": request.get("params", {}).get("input"), "receivedModel": request.get("params", {}).get("model")}})
        elif method == "turn/interrupt":
            _send({"jsonrpc": "2.0", "id": request_id, "result": {"interrupted": True}})
        elif method == "test/timeout":
            continue
        elif method == "test/approval":
            _send({"jsonrpc": "2.0", "id": 900, "method": "command/approval/request", "params": {"command": "del all"}})
            _send({"jsonrpc": "2.0", "id": request_id, "result": {"approvalRequestSent": True}})
        else:
            _send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "method not found"}})


if __name__ == "__main__":
    main()
