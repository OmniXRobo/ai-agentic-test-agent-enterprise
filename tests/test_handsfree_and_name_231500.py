"""23.15.0: "Listen always" in the app and the extension, and the agent's new name.

The user asked not to press a button before every sentence ("i have not to click all the time for talk") and for a
name that says what the page does. The front end is plain ES modules with no build step, so these checks read the
shipped files: the wiring has to be there, exported where it is imported, and the old name gone from what people see.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
EXT = ROOT / "browser-extension" / "extension"
NAME = "Workflow Operator"


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ---- the app's voice popup --------------------------------------------------------------------

def test_voice_client_offers_hands_free_listening():
    src = read(WEB / "js" / "voice.js")
    assert "export async function setHandsFree" in src
    assert "export function handsFreeActive" in src
    assert "voice.handsFree" in src  # the wake loop acts on each sentence instead of waiting for a click


def test_the_popup_has_the_listen_always_button_and_imports_what_it_uses():
    src = read(WEB / "js" / "voice-agent.js")
    imported = re.search(r"import \{([^}]+)\} from \"\./voice\.js\";", src)
    assert imported, "the panel must import the voice client"
    names = {n.strip() for n in imported.group(1).replace("\n", " ").split(",")}
    assert {"setHandsFree", "handsFreeActive"} <= names    # 23.15.0: these were used before they were imported
    assert "vaHands" in src and "Listen always" in src


# ---- the extension ---------------------------------------------------------------------------

def test_the_recorder_can_end_a_sentence_by_itself():
    src = read(EXT / "lib" / "wav-recorder.js")
    assert "silenceMs" in src and "heard:" in src


def test_the_extension_side_panel_listens_always_and_stops_cleanly():
    src = read(EXT / "sidepanel.js")
    assert "handsBtn" in src and "Listen always" in src
    assert "async function handsLoop" in src and "function handsFree" in src
    assert 'if (tab !== "chat") S.hands = false;' in src       # leaving the chat releases the microphone
    assert "if (!r.heard()) continue;" in src                  # silence is not sent to the recognizer


# ---- the name ---------------------------------------------------------------------------------

def test_the_app_calls_it_the_workflow_operator():
    app, page = read(WEB / "js" / "app.js"), read(WEB / "js" / "pages" / "ai-agent.js")
    assert f'["ai-agent", "{NAME}", aiAgent]' in app          # the route is unchanged, so saved links still work
    assert f"<h2 style=\"margin:0\">{NAME}</h2>" in page
    assert '"AI Agent", aiAgent' not in app and ">AI Agent<" not in page


def test_the_extension_carries_the_same_name():
    m = json.loads(read(EXT / "manifest.json"))
    assert m["name"] == f"SkyGuard {NAME}" and m["short_name"] == NAME
    assert m["action"]["default_title"] == f"SkyGuard {NAME}"
    assert m["version"] == "1.5.0"
    for f in ("sidepanel.html", "options.html"):
        assert NAME in read(EXT / f) and "AI Agent" not in read(EXT / f)


def test_the_page_can_be_opened_by_voice_under_either_name():
    from qa_platform.voice.intents import parse

    for said in ("open workflow operator", "open the ai agent"):
        assert parse(said).params == {"route": "ai-agent"}, said


@pytest.mark.parametrize("doc", ["INSTALL_CHROME.md", "INSTALL_EDGE.md", "UNINSTALL.md"])
def test_the_extension_documents_the_name_people_will_see(doc):
    src = read(ROOT / "browser-extension" / "docs" / doc)
    assert f"SkyGuard {NAME}" in src and "Adaptive AI Agent" not in src
