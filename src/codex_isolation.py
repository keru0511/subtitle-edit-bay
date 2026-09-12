from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterator, Mapping, Protocol, Sequence


MAX_MCP_STATUS_PAGE_SIZE = 100
MAX_MCP_STATUS_PAGES = 100
MAX_ISOLATED_ID_CHARS = 160
CODEX_APPS_MCP_SERVER_NAME = "codex_apps"

# These feature names are intentionally kept in one place.  A proposal or a
# review must not inherit newly enabled tools merely because the app-server
# gained another default feature.
ISOLATED_DISABLED_FEATURES = (
    "apps",
    "code_mode",
    "code_mode_only",
    "enable_mcp_apps",
    "image_generation",
    "multi_agent",
    "multi_agent_v2",
    "plugins",
    "shell_tool",
    "skill_mcp_dependency_install",
    "standalone_web_search",
    "tool_suggest",
    "unified_exec",
    "view_image",
    "web_search_cached",
    "web_search_request",
)


class CodexIsolationError(ValueError):
    """Raised when an app-server turn cannot be isolated safely."""


class McpStatusClient(Protocol):
    def mcp_server_status_list(self, **kwargs: Any) -> Mapping[str, Any]: ...


def validate_isolated_cwd(value: str | Path | None) -> str:
    """Validate the empty directory used as an app-server turn cwd."""

    if value is None:
        raise CodexIsolationError("Codex turn requires an isolated working directory")
    try:
        path = Path(value).resolve(strict=True)
    except OSError as error:
        raise CodexIsolationError("Codex turn working directory is unavailable") from error
    if not path.is_dir():
        raise CodexIsolationError("Codex turn working directory must be a directory")
    try:
        if any(path.iterdir()):
            raise CodexIsolationError("Codex turn working directory must be empty")
    except OSError as error:
        raise CodexIsolationError("Codex turn working directory is unavailable") from error
    return str(path)


@contextmanager
def isolated_codex_cwd(prefix: str = "subtitle-edit-bay-codex-") -> Iterator[str]:
    """Create an empty temporary cwd and remove it after the turn finishes."""

    with TemporaryDirectory(prefix=prefix) as temporary:
        yield validate_isolated_cwd(temporary)


def collect_mcp_server_names(
    client: McpStatusClient,
    *,
    thread_id: str = "",
    config_only: bool = False,
) -> tuple[str, ...]:
    """Read a bounded MCP inventory for a fail-closed isolation check."""

    names: list[str] = []
    cursor = ""
    seen_cursors: set[str] = set()
    for _page_index in range(MAX_MCP_STATUS_PAGES):
        params: dict[str, Any] = {
            "limit": MAX_MCP_STATUS_PAGE_SIZE,
            "detail": "toolsAndAuthOnly",
        }
        if cursor:
            params["cursor"] = cursor
        if thread_id:
            params["thread_id"] = thread_id
        payload = client.mcp_server_status_list(**params)
        data = payload.get("data")
        if not isinstance(data, list):
            raise CodexIsolationError("Codex MCP inventory is invalid")
        for item in data:
            if not isinstance(item, Mapping):
                raise CodexIsolationError("Codex MCP inventory is invalid")
            name = item.get("name")
            if not isinstance(name, str) or not name.strip() or len(name) > MAX_ISOLATED_ID_CHARS:
                raise CodexIsolationError("Codex MCP inventory contains an invalid server name")
            if config_only and (
                item.get("pluginId") is not None or name == CODEX_APPS_MCP_SERVER_NAME
            ):
                continue
            names.append(name)
        next_cursor = payload.get("nextCursor")
        if next_cursor is None:
            return tuple(dict.fromkeys(names))
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen_cursors:
            raise CodexIsolationError("Codex MCP inventory pagination is invalid")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    raise CodexIsolationError("Codex MCP inventory exceeds the page limit")


def build_isolated_thread_config(mcp_server_names: Sequence[str]) -> dict[str, Any]:
    """Build the shared fail-closed thread config for review/proposal turns."""

    return {
        "features": {name: False for name in ISOLATED_DISABLED_FEATURES},
        "include_apps_instructions": False,
        "include_collaboration_mode_instructions": False,
        "include_environment_context": False,
        "include_permissions_instructions": False,
        "mcp_servers": {name: {"enabled": False} for name in mcp_server_names},
        "memories": {"dedicated_tools": False, "use_memories": False},
        "skills": {"include_instructions": False},
        "tools": {
            "experimental_request_user_input": {"enabled": False},
            "update_plan": {"enabled": False},
        },
        "web_search": "disabled",
    }


def build_isolated_thread_params(
    cwd: str | Path,
    *,
    mcp_server_names: Sequence[str],
) -> dict[str, Any]:
    """Build the shared camelCase app-server ``thread/start`` payload."""

    isolated_cwd = validate_isolated_cwd(cwd)
    return {
        "approvalPolicy": "never",
        "config": build_isolated_thread_config(mcp_server_names),
        "cwd": isolated_cwd,
        "dynamicTools": [],
        "environments": [],
        "sandbox": "read-only",
        "ephemeral": True,
        "runtimeWorkspaceRoots": [],
    }


def build_isolated_turn_kwargs(cwd: str | Path) -> dict[str, Any]:
    """Build the shared snake_case ``run_structured_turn`` payload."""

    return {
        "cwd": validate_isolated_cwd(cwd),
        "environments": [],
        "approval_policy": "never",
        "runtime_workspace_roots": [],
        "sandbox_policy": {"type": "readOnly", "networkAccess": False},
    }
