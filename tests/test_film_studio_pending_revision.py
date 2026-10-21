"""Studio must reconcile external revisions after busy work or stale drags finish."""
from pathlib import Path


def test_studio_queues_external_revisions_until_safe_to_reload():
    source = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    assert "pendingExternalRevision:''" in source
    assert "function flushPendingExternalRevision()" in source
    assert "if(!S.pendingExternalRevision||!S.open||S.busy||S.drag||S.envelopeDrag)return false;" in source
    assert "S.pendingExternalRevision=data.revision;" in source
    assert "flushPendingExternalRevision();" in source[source.index("async function filmStudioCommit"):source.index("async function filmStudioHistory")]


def test_studio_discards_timeline_and_envelope_drag_when_external_revision_arrives():
    source = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    assert "本次旧画面拖动未提交，先载入外部版本" in source
    assert "本次拖动未提交，先载入外部版本" in source
    assert "S.pendingExternalRevision" in source[source.index("async function onUp"):source.index("document.addEventListener('pointermove',onUp)") if "document.addEventListener('pointermove',onUp)" in source else source.index("document.addEventListener('pointermove',onMove)")]
    pointer_cancel = source[source.index("document.addEventListener('pointercancel'"):source.index("window.filmStudioSetSideMode")]
    assert "flushPendingExternalRevision();" in pointer_cancel
