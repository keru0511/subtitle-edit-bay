from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

from openai_codex import CodexConfig, TurnHandle
from openai_codex.client import CodexClient
from openai_codex.errors import TransportClosedError
from openai_codex.generated.v2_all import ChatgptLoginAccountResponse, TurnInterruptResponse

from src.ai_provider import AIProviderEvent, AIProviderPrompt
from src.codex_ai_provider import CodexAIProvider
from src.codex_highlight_ranker import rank_highlight_candidates
from src.codex_isolation import build_isolated_thread_params, collect_mcp_server_names, isolated_codex_cwd
from src.codex_sdk import create_codex_client, reject_server_request, run_structured_turn, sdk_deadline
from src.data_boundary import decode_json, is_string_object_mapping
from src.gui_codex_state import CodexSessionController
from tests.typed_case import TypedTestCase


class CodexSdkTests(TypedTestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.requests = self.root / "requests.jsonl"
        self.client = CodexClient(
            CodexConfig(
                cwd=str(Path(__file__).resolve().parents[1]),
                launch_args_override=(sys.executable, "-m", "tests.fake_codex_app_server", str(self.requests)),
                env={"PYTHONUTF8": "1"},
            ),
            approval_handler=reject_server_request,
        )
        self.addCleanup(self.client.close)
        with sdk_deadline(self.client):
            self.client.start()
            self.client.initialize()

    def test_factory_uses_sdk_bundled_runtime(self) -> None:
        client = create_codex_client(self.root)
        self.assertIsNone(client.config.codex_bin)
        self.assertIsNone(client.config.launch_args_override)
        self.assertEqual(client.config.cwd, str(self.root))

    def test_account_models_login_completion_and_resume(self) -> None:
        self.assertFalse(self.client.account_read().requires_openai_auth)
        login = self.client.account_login_start({"type": "chatgpt"}).root
        assert isinstance(login, ChatgptLoginAccountResponse)
        self.assertEqual(login.auth_url, "https://example.invalid/login")
        self.assertTrue(self.client.wait_for_login_completed(login.login_id).success)
        self.client.account_login_cancel(login.login_id)
        self.client.account_logout()
        self.assertEqual(self.client.model_list().data[0].id, "fixture")
        self.assertEqual(self.client.thread_resume("fixture-thread").thread.id, "fixture-thread")

    def test_structured_result_progress_and_isolation_use_sdk(self) -> None:
        empty = self.root / "empty"
        empty.mkdir()
        self.assertEqual(collect_mcp_server_names(self.client), ())
        params = build_isolated_thread_params(empty, mcp_server_names=("configured-mcp",))
        thread_id = self.client.thread_start(params).thread.id
        self.assertEqual(collect_mcp_server_names(self.client, thread_id=thread_id), ())
        methods: list[str] = []
        result = run_structured_turn(
            self.client,
            thread_id=thread_id,
            prompt="字幕を整える",
            output_schema={"type": "object"},
            context={"segments": []},
            cwd=str(empty),
            on_event=lambda event: methods.append(event.method),
        )
        self.assertEqual(result, {"summary": "修正", "operations": [], "warnings": []})
        self.assertIn("item/agentMessage/delta", methods)
        self.assertIn("turn/completed", methods)
        requests = [decode_json(line) for line in self.requests.read_text(encoding="utf-8").splitlines()]
        turns = [item for item in requests if is_string_object_mapping(item) and item.get("method") == "turn/start"]
        self.assertEqual(len(turns), 1)
        turn = turns[0]
        assert is_string_object_mapping(turn)
        values = turn["params"]
        assert is_string_object_mapping(values)
        self.assertEqual(values["outputSchema"], {"type": "object"})
        self.assertEqual(values["runtimeWorkspaceRoots"], [])
        self.assertEqual(values["environments"], [])
        self.assertEqual(values["sandboxPolicy"], {"type": "readOnly", "networkAccess": False})

    def test_ranker_validation_failure_preserves_the_live_sdk_client(self) -> None:
        candidates: list[dict[str, object]] = [{"id": "h1", "start": 0.0, "end": 5.0, "score": 0.5}]

        result = rank_highlight_candidates(candidates, client=self.client)

        self.assertTrue(result.fallback)
        self.assertEqual(self.client.model_list().data[0].id, "fixture")

    def test_invalid_empty_and_oversized_structured_outputs_are_rejected(self) -> None:
        thread_id = self.client.thread_start().thread.id
        for prompt in ("invalid-json", "no-message", "oversize"):
            with self.subTest(prompt=prompt), self.assertRaises((ValueError, json.JSONDecodeError)):
                run_structured_turn(
                    self.client, thread_id=thread_id, prompt=prompt, output_schema={"type": "object"}, context={}
                )

    def test_isolated_session_closes_sdk_before_workspace_cleanup(self) -> None:
        for prompt, expected_state in (("review", "proposal_ready"), ("invalid-json", "error")):
            with self.subTest(prompt=prompt):
                clients: list[CodexClient] = []
                workspaces: list[Path] = []
                closed_before_cleanup: list[bool] = []

                def create_client(cwd: str | None) -> CodexClient:
                    assert cwd is not None
                    workspaces.append(Path(cwd))
                    client = CodexClient(
                        CodexConfig(
                            cwd=cwd,
                            launch_args_override=(
                                sys.executable,
                                "-m",
                                "tests.fake_codex_app_server",
                                str(self.requests),
                            ),
                            env={"PYTHONUTF8": "1", "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
                        ),
                        approval_handler=reject_server_request,
                    )
                    self.addCleanup(client.close)
                    clients.append(client)
                    return client

                @contextmanager
                def checked_workspace() -> Iterator[str]:
                    with isolated_codex_cwd() as cwd:
                        try:
                            yield cwd
                        finally:
                            # Windows cannot delete a live child process's cwd. Check
                            # that SDK shutdown precedes deletion on every platform.
                            with self.assertRaises(TransportClosedError):
                                clients[0].model_list()
                            closed_before_cleanup.append(True)

                controller = CodexSessionController(
                    client_factory=create_client, isolated_turn=True, proposal_parser=lambda payload: payload
                )
                self.addCleanup(controller.stop)
                with patch("src.gui_codex_state.isolated_codex_cwd", checked_workspace):
                    controller.start(prompt=prompt, context={})
                    worker = controller._thread
                    assert worker is not None
                    worker.join(5)
                self.assertFalse(worker.is_alive())
                self.assertEqual(controller.snapshot.state, expected_state)
                self.assertEqual(closed_before_cleanup, [True])
                self.assertFalse(workspaces[0].exists())

    def test_final_answer_takes_precedence_over_later_commentary(self) -> None:
        thread_id = self.client.thread_start().thread.id
        result = run_structured_turn(
            self.client, thread_id=thread_id, prompt="phase-order", output_schema={"type": "object"}, context={}
        )
        self.assertEqual(result["summary"], "修正")

    def test_provider_receives_typed_sdk_notifications_before_start_response(self) -> None:
        self.client.close()
        provider = CodexAIProvider(
            client_factory=lambda: CodexClient(self.client.config, approval_handler=reject_server_request)
        )
        self.addCleanup(provider.close)
        events: list[AIProviderEvent] = []
        completed = threading.Event()

        def receive(event: AIProviderEvent) -> None:
            events.append(event)
            if event.kind == "turn_completed":
                completed.set()

        provider.subscribe(receive)
        self.assertEqual(provider.connect().auth_state, "authenticated")
        session = provider.new_session(model_id="fixture", workspace_root=str(self.root))
        provider.send_prompt(
            AIProviderPrompt(session=session, prompt="こんにちは", model_id="fixture", workspace_root=str(self.root))
        )
        self.assertTrue(completed.wait(5))
        self.assertEqual([event.text for event in events if event.kind == "message_completed"], ["SDK応答"])
        self.assertEqual(events[-1].status, "completed")

    def test_interrupt_uses_sdk_turn_handle(self) -> None:
        thread_id = self.client.thread_start().thread.id
        started = self.client.turn_start(thread_id, "wait")
        handle = TurnHandle(self.client, thread_id, started.turn.id)
        handle.interrupt()
        self.assertEqual(handle.run().status.value, "interrupted")

    def test_command_and_file_requests_are_explicitly_declined(self) -> None:
        for method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
            self.assertEqual(reject_server_request(method, {}), {"decision": "decline"})
        self.client.request("test/approval", {}, response_model=TurnInterruptResponse)
        self.assertIn('"decision": "decline"', self.requests.read_text(encoding="utf-8"))

    def test_unknown_server_request_fails_instead_of_auto_approval(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "未対応"):
            self.client.request("test/unknown", {}, response_model=TurnInterruptResponse)

    def test_deadline_closes_stalled_sdk_connection(self) -> None:
        with self.assertRaises(TimeoutError), sdk_deadline(self.client, 0.1):
            self.client.request("test/timeout", {}, response_model=TurnInterruptResponse)
        with self.assertRaises(TransportClosedError):
            self.client.model_list()

    def test_unexpected_process_exit_releases_sdk_waiter(self) -> None:
        with self.assertRaises(TransportClosedError):
            self.client.request("test/exit", {}, response_model=TurnInterruptResponse)

    def test_bundled_runtime_initializes(self) -> None:
        """Run the pinned runtime on Linux and native Windows without a login."""
        runtime_home = self.root / "日本語 SDK"
        runtime_home.mkdir()
        # This is the child runtime's normal configuration, not the host account.
        client = CodexClient(
            CodexConfig(cwd=str(runtime_home), env={"CODEX_HOME": str(runtime_home)}),
            approval_handler=reject_server_request,
        )
        try:
            with sdk_deadline(client):
                client.start()
                metadata = client.initialize()
            self.assertIn("0.163.0", metadata.userAgent or "")
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
