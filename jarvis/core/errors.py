"""Exception hierarchy shared by all JARVIS components."""


class JarvisError(Exception):
    """Base class for all JARVIS errors."""


class ConfigError(JarvisError):
    """Invalid or unreadable configuration."""


class LLMError(JarvisError):
    """Base class for language-model backend errors."""


class LLMUnavailableError(LLMError):
    """The LLM backend (Ollama) is not running or not reachable."""


class ModelNotFoundError(LLMError):
    """The requested model is not installed in the backend."""


class ToolError(JarvisError):
    """A tool failed or was called with invalid arguments."""


class PermissionDenied(JarvisError):
    """The user (or the policy) refused to allow an action."""


class TaskCancelled(JarvisError):
    """The current task was cancelled (stop button / emergency stop)."""
