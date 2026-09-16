"""Workspace navigation state shared by the production GUI facade.

The normal video editor and the short artifact editor are separate workspace
contracts.  This module owns the navigation state and the player-position
boundary without importing Qt or keeping a reference to ``EditBayBackend``.
The facade is responsible only for adapting the result to QML properties and
signals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


NORMAL_VIDEO_WORKSPACE = "normal-video"
SHORT_ARTIFACT_WORKSPACE = "short-artifact"
WORKSPACE_KINDS = (NORMAL_VIDEO_WORKSPACE, SHORT_ARTIFACT_WORKSPACE)


@dataclass(frozen=True)
class WorkspacePlayerState:
    """Transport state owned by one workspace."""

    position_ms: int = 0
    playing: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "positionMs": self.position_ms,
            "playing": self.playing,
        }


@dataclass(frozen=True)
class WorkspaceSwitchResult:
    """Result of a guarded workspace transition."""

    accepted: bool
    changed: bool = False
    reason: str = ""
    previous: str = NORMAL_VIDEO_WORKSPACE
    current: str = NORMAL_VIDEO_WORKSPACE
    restored_position_ms: int = 0


class WorkspaceNavigationController:
    """Own workspace identity and isolated normal/short transport state.

    ``running`` and ``active_job`` are passed into ``switch_workspace`` as
    snapshots.  That keeps the controller independent from the Qt backend and
    makes the fail-closed navigation rule deterministic in unit tests.
    """

    def __init__(self) -> None:
        self._current_workspace = NORMAL_VIDEO_WORKSPACE
        self._player_states: dict[str, WorkspacePlayerState] = {
            NORMAL_VIDEO_WORKSPACE: WorkspacePlayerState(),
            SHORT_ARTIFACT_WORKSPACE: WorkspacePlayerState(),
        }

    @property
    def current_workspace(self) -> str:
        return self._current_workspace

    @property
    def normal_video_player(self) -> WorkspacePlayerState:
        return self._player_states[NORMAL_VIDEO_WORKSPACE]

    @property
    def short_artifact_player(self) -> WorkspacePlayerState:
        return self._player_states[SHORT_ARTIFACT_WORKSPACE]

    @property
    def current_player(self) -> WorkspacePlayerState:
        return self._player_states[self._current_workspace]

    def player_state(self, workspace: str) -> WorkspacePlayerState | None:
        return self._player_states.get(str(workspace).strip())

    def player_states(self) -> dict[str, dict[str, Any]]:
        return {
            workspace: state.as_dict()
            for workspace, state in self._player_states.items()
        }

    def update_player_state(
        self,
        workspace: str,
        position_ms: int,
        *,
        playing: bool = False,
    ) -> bool:
        """Store one workspace's transport state without touching the other."""

        key = str(workspace).strip()
        if key not in WORKSPACE_KINDS:
            return False
        next_state = WorkspacePlayerState(
            position_ms=max(0, int(position_ms)),
            playing=bool(playing),
        )
        if self._player_states[key] == next_state:
            return False
        self._player_states[key] = next_state
        return True

    def switch_workspace(
        self,
        workspace: str,
        *,
        running: bool = False,
        active_job: str = "",
    ) -> WorkspaceSwitchResult:
        """Switch workspace only when no processing job is active.

        The currently visible player is marked stopped at the boundary, while
        its last position remains intact.  The returned position belongs to
        the target workspace and is never inferred from the source workspace.
        """

        target = str(workspace).strip()
        previous = self._current_workspace
        if target not in WORKSPACE_KINDS:
            return WorkspaceSwitchResult(
                accepted=False,
                reason=f"未知のワークスペースです: {target or '（未指定）'}",
                previous=previous,
                current=previous,
                restored_position_ms=self.current_player.position_ms,
            )

        if bool(running) or bool(str(active_job).strip()):
            return WorkspaceSwitchResult(
                accepted=False,
                reason="処理中はワークスペースを切り替えできません",
                previous=previous,
                current=previous,
                restored_position_ms=self.current_player.position_ms,
            )

        if target == previous:
            return WorkspaceSwitchResult(
                accepted=True,
                previous=previous,
                current=target,
                restored_position_ms=self.current_player.position_ms,
            )

        current_player = self.current_player
        if current_player.playing:
            self._player_states[previous] = WorkspacePlayerState(
                position_ms=current_player.position_ms,
                playing=False,
            )
        self._current_workspace = target
        return WorkspaceSwitchResult(
            accepted=True,
            changed=True,
            previous=previous,
            current=target,
            restored_position_ms=self.current_player.position_ms,
        )


__all__ = [
    "NORMAL_VIDEO_WORKSPACE",
    "SHORT_ARTIFACT_WORKSPACE",
    "WORKSPACE_KINDS",
    "WorkspaceNavigationController",
    "WorkspacePlayerState",
    "WorkspaceSwitchResult",
]
