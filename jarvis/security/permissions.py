"""Permission policy.

* SAFE tools run automatically.
* MODERATE tools run automatically (or ask, if ``confirm_moderate``).
* DANGEROUS tools (delete files, send mail, install software, run unknown
  executables, dangerous shell commands, important system settings)
  ALWAYS need an explicit "yes" from the user via the confirm callback.
  With no confirm callback attached (e.g. headless), they are refused.

Every decision and every executed action is written to the audit journal
(``audit_log`` table) by the agent.
"""

from __future__ import annotations

import enum
import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

from jarvis.settings.schema import SecuritySettings
from jarvis.tools.base import RiskLevel, Tool

log = logging.getLogger(__name__)


class Decision(str, enum.Enum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True)
class ConfirmRequest:
    tool: str
    risk: RiskLevel
    summary: str
    arguments: dict[str, Any]


ConfirmCallback = Callable[[ConfirmRequest], bool]


class PermissionManager:
    def __init__(self, settings: SecuritySettings, confirm: ConfirmCallback | None = None) -> None:
        self.settings = settings
        self._confirm = confirm
        self._lock = threading.Lock()  # one confirmation dialog at a time

    def set_confirm_callback(self, confirm: ConfirmCallback | None) -> None:
        self._confirm = confirm

    def needs_confirmation(self, tool: Tool) -> bool:
        if tool.risk is RiskLevel.DANGEROUS:
            return True
        return tool.risk is RiskLevel.MODERATE and self.settings.confirm_moderate

    def check(self, tool: Tool, arguments: dict[str, Any], risk: RiskLevel | None = None) -> tuple[Decision, str]:
        """Decide whether ``tool`` may run. ``risk`` can raise the static level
        (e.g. a shell tool that classifies its command as dangerous)."""
        if tool.name in self.settings.blocked_tools:
            return Decision.DENY, "tool is blocked in settings"
        effective = risk or tool.risk
        must_ask = effective is RiskLevel.DANGEROUS or (
            effective is RiskLevel.MODERATE and self.settings.confirm_moderate
        )
        if not must_ask:
            return Decision.ALLOW, "auto"
        if self._confirm is None:
            return Decision.DENY, "confirmation required but no interactive user is available"
        req = ConfirmRequest(tool.name, effective, tool.describe_call(arguments), arguments)
        with self._lock:
            try:
                ok = bool(self._confirm(req))
            except Exception:  # noqa: BLE001
                log.exception("Confirm callback failed; denying")
                ok = False
        return (Decision.ALLOW, "confirmed by user") if ok else (Decision.DENY, "declined by user")
