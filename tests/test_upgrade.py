"""An existing 23.2.2 settings file (wake name Captain, push-to-talk, TFS and AI set) opened by 23.3.0."""
import json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(os.environ.get("APP_DIR", Path(__file__).resolve().parents[1] / "app"))))

OLD = {"server": {"host": "127.0.0.1", "port": 8765},
       "tfs": {"mode": "offline", "offline": {"url": "http://tfs.local:8080/tfs", "collection": "DefaultCollection"}},
       "ai": {"provider": "local", "base_url": "http://127.0.0.1:8080/v1", "model": "qwen2.5-7b"},
       "voice": {"agent_command_name": "Captain", "agent_display_name": "CAPTAIN", "listen_mode": "push_to_talk",
                 "phrase_style": "standard", "stt_engine": "builtin"}}


def test_upgrade_keeps_settings_and_turns_wake_on(tmp_path, monkeypatch):
    (tmp_path / "settings.json").write_text(json.dumps(OLD))
    monkeypatch.setenv("QA_HOME", str(tmp_path))
    from fastapi.testclient import TestClient
    from qa_platform.main import create_app
    with TestClient(create_app()) as c:
        v = c.get("/api/voice/settings").json()
        s = c.get("/api/settings").json()["settings"]
    assert v["agent_command_name"] == "Captain" and v["stt_engine"] == "builtin"
    assert v["wake_listening"] is True and v["address_as"] == "Boss"
    assert v["phrase_style"] == "standard"  # the patch script switches it; the app itself never rewrites it
    for k, val in OLD["tfs"]["offline"].items():
        assert s["tfs"]["offline"][k] == val
    assert s["tfs"]["mode"] == "offline"
    for k, val in OLD["ai"].items():
        assert s["ai"][k] == val
