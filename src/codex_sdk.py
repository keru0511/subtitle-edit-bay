"""Application settings and deadlines around the official Codex SDK."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Mapping, cast

from openai_codex import CodexConfig
from openai_codex.client import CodexClient
from openai_codex.generated.v2_all import (
    AgentMessageThreadItem,
    ItemCompletedNotification,
    MessagePhase,
    TurnCompletedNotification,
)
from openai_codex.models import JsonObject, Notification

from .data_boundary import decode_json, is_string_object_mapping

RPC_TIMEOUT = 30.0
PROPOSAL_TIMEOUT = 120.0
MAX_STRUCTURED_OUTPUT_BYTES = 128 * 1024


def reject_server_request(method: str, _params: JsonObject | None) -> JsonObject:
    if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
        return {"decision": "decline"}
    # Raising releases SDK waiters; callers close this connection on failure.
    raise RuntimeError("Codexから未対応の要求を受信しました")


def create_codex_client(cwd: str | Path | None = None) -> CodexClient:
    return CodexClient(
        CodexConfig(
            cwd=str(cwd) if cwd is not None else None,
            client_name="subtitle_edit_bay",
            client_title="Subtitle Edit Bay",
        ),
        approval_handler=reject_server_request,
    )


@contextmanager
def sdk_deadline(client: CodexClient, seconds: float = RPC_TIMEOUT) -> Iterator[None]:
    """A stalled SDK call must release its worker and allow a fresh connection."""
    expired = threading.Event()

    def expire() -> None:
        expired.set()
        client.close()

    timer = threading.Timer(seconds, expire)
    timer.daemon = True
    timer.start()
    try:
        yield
        if expired.is_set():
            raise TimeoutError("Codexの応答がタイムアウトしました。再接続してください")
    except Exception as error:
        if expired.is_set():
            raise TimeoutError("Codexの応答がタイムアウトしました。再接続してください") from error
        raise
    finally:
        timer.cancel()


def run_structured_turn(
    client: CodexClient,
    *,
    thread_id: str,
    prompt: str,
    output_schema: Mapping[str, object],
    context: Mapping[str, object],
    cwd: str | None = None,
    on_event: Callable[[Notification], None] | None = None,
    timeout: float = PROPOSAL_TIMEOUT,
) -> dict[str, object]:
    params: JsonObject = {
        "approvalPolicy": "never",
        "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
        "outputSchema": cast(JsonObject, dict(output_schema)),
    }
    if cwd is not None:
        params.update({"cwd": cwd, "environments": [], "runtimeWorkspaceRoots": []})
    inputs: list[JsonObject] = [
        {"type": "text", "text": prompt},
        {"type": "text", "text": "参照コンテキスト(JSON):\n" + json.dumps(context, ensure_ascii=False)},
    ]
    text: str | None = None
    final_answer_received = False
    with sdk_deadline(client, timeout):
        with sdk_deadline(client):
            started = client.turn_start(thread_id, inputs, params)
        try:
            while True:
                event = client.next_turn_notification(started.turn.id)
                if on_event is not None:
                    on_event(event)
                payload = event.payload
                if isinstance(payload, ItemCompletedNotification):
                    item = payload.item.root
                    if isinstance(item, AgentMessageThreadItem):
                        if item.phase == MessagePhase.final_answer:
                            text = item.text
                            final_answer_received = True
                        elif item.phase is None and not final_answer_received:
                            text = item.text
                elif isinstance(payload, TurnCompletedNotification):
                    if payload.turn.status.value != "completed":
                        raise RuntimeError("Codexの編集案生成が完了しませんでした")
                    break
        finally:
            client.unregister_turn_notifications(started.turn.id)
    if text is None or len(text.encode("utf-8")) > MAX_STRUCTURED_OUTPUT_BYTES:
        raise ValueError("Codexの構造化出力が空か、サイズ上限を超えています")
    proposal_payload = decode_json(text)
    if not is_string_object_mapping(proposal_payload):
        raise ValueError("Codexの構造化出力がJSONオブジェクトではありません")
    return dict(proposal_payload)
