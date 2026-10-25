"""App-server fixture for integration tests against the official SDK."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TextIO, cast

from src.data_boundary import is_object_list, is_string_object_mapping


def send(value: dict[str, object]) -> None:
    print(json.dumps(value, ensure_ascii=False), flush=True)


def notification(method: str, params: dict[str, object]) -> None:
    send({"method": method, "params": params})


def thread_response(thread_id: str) -> dict[str, object]:
    return {
        "approvalPolicy": "never",
        "approvalsReviewer": "user",
        "cwd": str(Path.cwd()),
        "model": "fixture",
        "modelProvider": "openai",
        "sandbox": {"type": "readOnly"},
        "thread": {
            "id": thread_id,
            "cliVersion": "0.163.0",
            "createdAt": 1,
            "updatedAt": 1,
            "cwd": str(Path.cwd()),
            "ephemeral": True,
            "modelProvider": "openai",
            "preview": "",
            "sessionId": "fixture-session",
            "source": "cli",
            "status": {"type": "idle"},
            "turns": [],
        },
    }


def main() -> None:
    log_path = Path(sys.argv[1])
    pending_approval: object = None
    pending_turn: str | None = None
    counter = 0
    for line in cast(TextIO, sys.stdin):
        message: object = json.loads(line)
        if not is_string_object_mapping(message):
            raise ValueError("invalid fixture request")
        with log_path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(message) + "\n")
        request_id = message.get("id")
        method = message.get("method")
        raw_params = message.get("params")
        params = raw_params if is_string_object_mapping(raw_params) else {}
        if method is None:
            if request_id == "fixture-approval" and pending_approval:
                send({"id": pending_approval, "result": message.get("result")})
                pending_approval = None
            continue
        if request_id is None:
            continue
        result: dict[str, object] = {}
        if method == "initialize":
            result = {"userAgent": "codex/0.163.0"}
        elif method == "account/read":
            result = {"account": None, "requiresOpenaiAuth": False}
        elif method == "account/login/start":
            result = {"type": "chatgpt", "loginId": "fixture-login", "authUrl": "https://example.invalid/login"}
            notification("account/login/completed", {"loginId": "fixture-login", "success": True})
        elif method == "account/login/cancel":
            result = {"status": "canceled"}
        elif method == "model/list":
            result = {
                "data": [
                    {
                        "id": "fixture",
                        "model": "fixture",
                        "displayName": "Fixture",
                        "description": "",
                        "hidden": False,
                        "isDefault": True,
                        "defaultReasoningEffort": "medium",
                        "supportedReasoningEfforts": [],
                    }
                ]
            }
        elif method in ("thread/start", "thread/resume"):
            result = thread_response(str(params.get("threadId", "fixture-thread")))
        elif method == "mcpServerStatus/list":
            result = {"data": [], "nextCursor": None}
        elif method == "turn/start":
            counter += 1
            turn_id = f"turn-{counter}"
            inputs = params.get("input")
            assert is_object_list(inputs) and inputs and is_string_object_mapping(inputs[0])
            prompt = str(inputs[0].get("text", ""))
            turn: dict[str, object] = {"id": turn_id, "items": [], "status": "inProgress"}
            result = {"turn": turn}
            notification("turn/started", {"threadId": "fixture-thread", "turn": turn})
            if prompt == "wait":
                pending_turn = turn_id
            else:
                proposal: dict[str, object] = {"summary": "修正", "operations": [], "warnings": []}
                text = json.dumps(proposal, ensure_ascii=False)
                if prompt == "invalid-json":
                    text = "invalid JSON"
                elif prompt == "oversize":
                    text = "x" * (128 * 1024 + 1)
                elif not params.get("outputSchema"):
                    text = "SDK応答"
                items: list[dict[str, object]] = []
                if prompt != "no-message":
                    notification(
                        "item/agentMessage/delta",
                        {"threadId": "fixture-thread", "turnId": turn_id, "itemId": "item-1", "delta": text},
                    )
                    item: dict[str, object] = {"id": "item-1", "type": "agentMessage", "text": text}
                    if prompt == "phase-order":
                        item["phase"] = "final_answer"
                    items.append(item)
                    notification(
                        "item/completed",
                        {"threadId": "fixture-thread", "turnId": turn_id, "item": item, "completedAtMs": 1},
                    )
                    if prompt == "phase-order":
                        notification(
                            "item/completed",
                            {
                                "threadId": "fixture-thread",
                                "turnId": turn_id,
                                "completedAtMs": 2,
                                "item": {"id": "after-final", "type": "agentMessage", "text": "補足のコメント"},
                            },
                        )
                notification(
                    "turn/completed",
                    {"threadId": "fixture-thread", "turn": {"id": turn_id, "items": items, "status": "completed"}},
                )
        elif method == "turn/interrupt":
            notification(
                "turn/completed",
                {"threadId": "fixture-thread", "turn": {"id": pending_turn, "items": [], "status": "interrupted"}},
            )
        elif method in ("test/approval", "test/unknown"):
            pending_approval = request_id
            request_method = "item/commandExecution/requestApproval" if method == "test/approval" else "item/tool/call"
            send({"id": "fixture-approval", "method": request_method, "params": {}})
            continue
        elif method == "test/timeout":
            continue
        elif method == "test/exit":
            return
        send({"id": request_id, "result": result})


if __name__ == "__main__":
    main()
