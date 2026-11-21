"""Exception hierarchy for the agent package."""
from __future__ import annotations


class AgentError(Exception):
    """Base class for every error raised by the agent package."""


# --- request -------------------------------------------------------------
class AgentRequestError(AgentError):
    """The caller's request is unusable (empty, bad task type, ...)."""


class InvalidImageError(AgentRequestError):
    """An uploaded image is missing, too large, or not a supported format."""


# --- run -----------------------------------------------------------------
class AgentOutputError(AgentError):
    """The model never produced a valid CoachResponse (after repair attempts)."""


class AgentLimitError(AgentError):
    """The run exceeded max_iterations / the tool-call budget."""


# --- LLM -----------------------------------------------------------------
class LlmError(AgentError):
    """Base class for LLM adapter failures."""


class LlmConfigError(LlmError):
    """Missing or invalid LLM configuration."""


class LlmCapabilityError(LlmError):
    """The configured model cannot do what the request needs (e.g. vision)."""


class LlmTimeoutError(LlmError):
    """The provider did not answer in time."""


class LlmProviderError(LlmError):
    """The provider returned an HTTP error or the connection failed."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LlmResponseError(LlmError):
    """The provider answered, but the payload is malformed."""


class MockLlmExhausted(LlmError):
    """MockLlmAdapter ran out of scripted responses."""


# --- tools / skills ------------------------------------------------------
class ToolRegistrationError(AgentError):
    """A tool could not be registered (bad name, duplicate, ...)."""


class SkillError(AgentError):
    """A SKILL.md file is malformed."""


class SkillNotFoundError(SkillError):
    """Requested skill does not exist."""
