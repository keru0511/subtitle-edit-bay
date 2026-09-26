"""Provider-neutral contract for the application's AI chat boundary.

The GUI only needs a small set of concepts from an AI provider: connection and
authentication state, models, sessions, and streaming turn events.  Protocol
details, such as JSON-RPC method names, stay in the provider adapter module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Protocol


@dataclass(frozen=True)
class AIModel:
    """A model exposed by one provider."""

    model_id: str
    display_name: str
    is_default: bool = False

    @property
    def id(self) -> str:
        """Return the stable provider-local model identifier."""

        return self.model_id

    def as_mapping(self) -> dict[str, object]:
        """Return the existing GUI-friendly model shape."""

        return {
            "id": self.model_id,
            "label": self.display_name,
            "is_default": self.is_default,
        }


@dataclass(frozen=True)
class AIProviderState:
    """Observable, provider-neutral state for an AI provider."""

    availability: str = "disconnected"
    auth_state: str = "unknown"
    auth_label: str = ""
    login_url: str = ""
    login_id: str = ""
    models: tuple[AIModel, ...] = ()
    model_selection_supported: bool = True
    selected_model: str = ""
    error: str = ""
    # True only when the provider exposes an official login action through
    # this boundary.  The GUI must not invent credential or OAuth input UI.
    login_available: bool = False

    @property
    def connection_state(self) -> str:
        """Compatibility alias for clients that call availability a connection state."""

        return self.availability

    @property
    def model_inventory(self) -> tuple[AIModel, ...]:
        """Return the provider's currently available model inventory."""

        return self.models

    @property
    def is_available(self) -> bool:
        return self.availability == "available"


@dataclass(frozen=True)
class AIProviderEvent:
    """A provider-neutral notification emitted during authentication or a turn."""

    kind: str
    session_id: str = ""
    turn_id: str = ""
    text: str = ""
    status: str = ""
    error: str = ""
    refresh_state: bool = False
    payload: Mapping[str, object] = field(default_factory=dict)

    @property
    def type(self) -> str:
        """Alias for callers that name event kinds by their type."""

        return self.kind


@dataclass(frozen=True)
class AIProviderSession:
    """An opaque provider session identifier owned by the adapter."""

    session_id: str


@dataclass(frozen=True)
class AIProviderPrompt:
    """Input for a provider turn."""

    session: AIProviderSession
    prompt: str
    model_id: str
    workspace_root: str

    @property
    def session_id(self) -> str:
        return self.session.session_id


@dataclass(frozen=True)
class AIProviderPromptResult:
    """Immediate result returned when a turn has been submitted."""

    turn_id: str = ""


AIProviderListener = Callable[[AIProviderEvent], None]


class AIProvider(Protocol):
    """Provider-neutral AI chat interface used by the GUI state controller."""

    @property
    def provider_id(self) -> str: ...

    @property
    def display_name(self) -> str: ...

    @property
    def state(self) -> AIProviderState: ...

    def subscribe(self, listener: AIProviderListener) -> None: ...

    def unsubscribe(self, listener: AIProviderListener) -> None: ...

    def connect(self, *, force: bool = False) -> AIProviderState: ...

    def refresh(self) -> AIProviderState: ...

    def login(self, *, relogin: bool = False) -> AIProviderState: ...

    def logout(self) -> AIProviderState: ...

    def select_model(self, model_id: str) -> AIProviderState: ...

    def new_session(
        self,
        *,
        model_id: str,
        workspace_root: str,
        existing_session_id: str = "",
    ) -> AIProviderSession: ...

    def send_prompt(self, request: AIProviderPrompt) -> AIProviderPromptResult: ...

    def cancel_active_turn(self, *, session_id: str, turn_id: str) -> None: ...

    def cancel(self, *, session_id: str, turn_id: str) -> None: ...

    def close(self) -> None: ...


# Short aliases make the boundary convenient for adapters without introducing
# provider-specific vocabulary into their public data model.
ProviderModel = AIModel
ProviderState = AIProviderState
ProviderEvent = AIProviderEvent
ProviderSession = AIProviderSession
PromptRequest = AIProviderPrompt
PromptResult = AIProviderPromptResult
AIProviderProtocol = AIProvider


__all__ = [
    "AIModel",
    "AIProvider",
    "AIProviderEvent",
    "AIProviderListener",
    "AIProviderPrompt",
    "AIProviderPromptResult",
    "AIProviderSession",
    "AIProviderState",
    "AIProviderProtocol",
    "PromptRequest",
    "PromptResult",
    "ProviderEvent",
    "ProviderModel",
    "ProviderSession",
    "ProviderState",
]
