"""The AI Director proposal/chat surface is React-owned when the built bundle exists."""
from pathlib import Path


def test_react_ai_director_keeps_the_existing_action_and_message_contract():
    root = Path(__file__).resolve().parents[1]
    html = (root / "webui" / "static" / "index.html").read_text(encoding="utf-8")
    studio = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    component = (root / "remotion_runtime" / "src" / "StudioCanvasInspector.tsx").read_text(encoding="utf-8")
    assert 'id="fs-react-ai-director"' in html
    assert 'id="fs-static-ai-director"' in html
    assert "const StudioAiDirector: React.FC" in component
    assert "window.filmStudioReactAi = {" in component
    assert "window.filmStudioReactAi" in studio
    assert "proposalOperationPreviews" in studio
    assert "operationPreviews:proposalPreview.entries" in studio
    assert 'data-react-proposal-details="true"' in component
    assert 'id="fs-proposal-operations"' in html
    assert "修改前：" in component and "修改后：" in component
    assert "function renderAiDirector()" in studio
    assert "renderAiDirector()" in studio
    assert 'id="fs-chat-log"' in component
    assert 'id="fs-chat-input"' in component
    for action in ("ask", "preview-proposal", "reject-proposal", "apply-proposal", "cancel-ai"):
        assert f'data-film-action="{action}"' in component
    assert "window.filmStudioCancelAi" in studio
    assert "readFilmAiStream" in studio
    assert "stream:true" in studio
    assert "on_token=on_delta" in (root / "webui" / "server.py").read_text(encoding="utf-8")
    server = (root / "webui" / "server.py").read_text(encoding="utf-8")
    assert 'route == "/api/editor/film/command/cancel"' in server
    assert "_FILM_AI_ACTIVE_STREAMS" in server
    assert "on_stream_open=on_stream_open, cancel_event=cancel_event" in server
    assert "stream.close()" in server
    assert "len(_FILM_AI_CANCELLED_REQUESTS) > 2048" in server
    assert "request_id:requestId" in studio
    assert "window.filmStudioCancelAi({preserveSteer:true})" in studio
    assert "S.messages.push({text:String(text),role:role});" in studio
    assert "baseRevision:String(S.proposal.base_revision||'')" in studio
