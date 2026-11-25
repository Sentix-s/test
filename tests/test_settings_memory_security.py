import pytest
import yaml

from jarvis.ai.types import Message, ToolCall
from jarvis.core.errors import ConfigError
from jarvis.memory import AuditLog, ConversationStore, Database, FactStore
from jarvis.security.permissions import Decision, PermissionManager
from jarvis.settings import Settings, load_settings, save_settings
from jarvis.settings.schema import SecuritySettings
from jarvis.tools.base import RiskLevel, Tool


def test_defaults_without_file(tmp_path):
    s = load_settings(tmp_path / "missing.yaml")
    assert s.ai.model == "qwen3:8b" and s.voice.idle_timeout_seconds == 15 and s.performance.profile.value == "balanced"


def test_save_only_overrides_and_roundtrip(tmp_path):
    p = tmp_path / "s.yaml"
    s = Settings()
    s.ai.model = "qwen2.5:7b-instruct"
    s.voice.idle_timeout_seconds = 30
    save_settings(s, p)
    assert yaml.safe_load(p.read_text(encoding="utf-8")) == {"ai": {"model": "qwen2.5:7b-instruct"},
                                                             "voice": {"idle_timeout_seconds": 30}}
    s2 = load_settings(p, {"ai": {"temperature": 0.1}})
    assert s2.ai.model == "qwen2.5:7b-instruct" and s2.ai.temperature == 0.1 and s2.voice.idle_timeout_seconds == 30


@pytest.mark.parametrize("content", ["voice:\n  idle_timeout_seconds: 7\n", "ai:\n  bogus: 1\n", "- a\n", "ai: [\n"])
def test_invalid_settings(tmp_path, content):
    p = tmp_path / "s.yaml"
    p.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(p)


def test_conversation_store(tmp_path):
    db = Database(tmp_path / "j.db")
    assert db.schema_version == 1
    store = ConversationStore(db)
    cid = store.create()
    store.add_message(cid, Message("user", "Привет"))
    store.add_message(cid, Message("assistant", "", tool_calls=[ToolCall("calculate", {"expression": "2+2"})]))
    store.add_message(cid, Message("tool", "4", tool_name="calculate"))
    msgs = store.messages(cid)
    assert [m.role for m in msgs] == ["user", "assistant", "tool"]
    assert msgs[1].tool_calls[0].arguments == {"expression": "2+2"} and msgs[2].tool_name == "calculate"
    assert [m.role for m in store.messages(cid, limit=2)] == ["assistant", "tool"]
    assert store.list()[0].message_count == 3 and store.latest_id() == cid
    db.close()
    # reopening keeps data and does not re-run migrations
    db2 = Database(tmp_path / "j.db")
    assert len(ConversationStore(db2).messages(cid)) == 3
    store2 = ConversationStore(db2)
    store2.delete(cid)
    assert db2.query("SELECT COUNT(*) AS n FROM messages")[0]["n"] == 0  # cascade


def test_fact_store_unicode_search():
    db = Database(":memory:")
    facts = FactStore(db)
    a = facts.add("Пользователя зовут Алексей", "personal")
    assert facts.add("Пользователя зовут Алексей") == a  # dedup
    facts.add("Любимый спорт — футбол", "preference")
    assert [f.content for f in facts.search("алексей")] == ["Пользователя зовут Алексей"]
    assert len(facts.search("")) == 2
    assert facts.delete(a) and not facts.delete(a)


def test_audit_log():
    db = Database(":memory:")
    log = AuditLog(db)
    log.record(tool="calculate", arguments={"expression": "1+1"}, risk="safe", decision="allow", ok=True, output="2")
    row = log.recent()[0]
    assert row["tool"] == "calculate" and row["ok"] == 1 and '"1+1"' in row["arguments"]


def _tool(risk):
    return Tool.from_function(lambda: "x", name="t", risk=risk)


def test_permissions():
    asked = []
    pm = PermissionManager(SecuritySettings(), confirm=lambda r: asked.append(r) or False)
    assert pm.check(_tool(RiskLevel.SAFE), {})[0] is Decision.ALLOW
    assert pm.check(_tool(RiskLevel.MODERATE), {})[0] is Decision.ALLOW
    assert pm.check(_tool(RiskLevel.DANGEROUS), {"path": "x"}) == (Decision.DENY, "declined by user")
    assert asked[0].summary == "t(path='x')"
    pm.set_confirm_callback(lambda r: True)
    assert pm.check(_tool(RiskLevel.DANGEROUS), {})[0] is Decision.ALLOW
    # a dynamic risk escalation is respected
    pm.set_confirm_callback(None)
    assert pm.check(_tool(RiskLevel.SAFE), {}, risk=RiskLevel.DANGEROUS)[0] is Decision.DENY
    pm2 = PermissionManager(SecuritySettings(confirm_moderate=True, blocked_tools=["t2"]), confirm=lambda r: False)
    assert pm2.check(_tool(RiskLevel.MODERATE), {})[0] is Decision.DENY
    assert pm2.check(Tool.from_function(lambda: "", name="t2"), {})[1] == "tool is blocked in settings"


def test_confirm_callback_exception_denies():
    def bad(_r):
        raise RuntimeError

    assert PermissionManager(SecuritySettings(), bad).check(_tool(RiskLevel.DANGEROUS), {})[0] is Decision.DENY
