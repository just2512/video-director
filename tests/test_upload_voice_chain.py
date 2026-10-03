# -*- coding: utf-8 -*-
"""§34 上传配音 compose 全链测试（上轮遗留项本轮落地）。"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from video_director import config


@pytest.fixture(scope="module")
def voice_audio(tmp_path_factory):
    """ffmpeg lavfi 生成 3 秒测试音频（voice_upload.Probe 需真实音频流）。"""
    ffmpeg = shutil.which("ffmpeg") or (Path(__file__).parents[1] / "release" / "bin" / "ffmpeg.exe")
    if not Path(ffmpeg).is_file():
        pytest.skip("无 ffmpeg")
    p = tmp_path_factory.mktemp("voice") / "narration.mp3"
    subprocess.run([str(ffmpeg), "-y", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=3", "-c:a", "libmp3lame", str(p)],
                   check=True)
    return str(p)


@pytest.fixture()
def _iso(tmp_path, monkeypatch):
    """隔离 storage + 锁估算路径（ASR 真实分支依赖模型下载，测试不触发网络）。"""
    (tmp_path / "composer").mkdir()
    monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path / "composer")
    # faster_whisper import 置 None → 函数内 try ImportError → 估算路径
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    yield


class TestUploadVoiceComposeChain:
    def test_import_uploaded_voice_contract(self, _iso, voice_audio):
        """§34/§16：Import→Probe→估算对齐→Narration 结构（ASR 双值枚举合法）。"""
        from video_director import voice_upload
        up = voice_upload.import_uploaded_voice(
            "uv-01", voice_audio, scene_texts=["第一句", "第二句"])
        for key in ("narration_file", "duration", "narration_scenes",
                    "word_timeline", "source", "tts_skipped"):
            assert key in up, f"缺 {key}"
        assert up["duration"] >= 2.5
        assert up["tts_skipped"] is True
        assert up["source"] in ("asr", "estimated")
        assert Path(up["narration_file"]).is_file()
        assert len(up["narration_scenes"]) == 2

    def test_asr_branch_preserved_in_source(self):
        """生产链 ASR 真实分支未被测试旁路破坏（faster-whisper import 保留）。"""
        src = Path("video_director/voice_upload.py").read_text(encoding="utf-8")
        assert "import faster_whisper" in src
        assert "build_aligned_narration_scenes" in src, "ASR 对齐链必须在源码保留"

    def test_invalid_media_rejected(self, _iso, tmp_path):
        """非媒体/无音频流文件诚实拒绝（不 fake）。"""
        from video_director import voice_upload
        fake = tmp_path / "fake.mp3"
        fake.write_bytes(b"not audio")
        with pytest.raises((voice_upload.VoiceUploadError, Exception)):
            voice_upload.import_uploaded_voice("uv-02", str(fake))

    def test_workflow_spec_passes_narration_upload(self):
        """§34：WorkflowRun spec.narration_upload → compose_auto 参数接线。"""
        from video_director import workflow_run as wf
        src = Path(wf.__file__).read_text(encoding="utf-8")
        assert "narration_upload=narration_upload" in src
        from video_director import pipeline as pl
        psrc = Path(pl.__file__).read_text(encoding="utf-8")
        assert "narration_upload: Optional[str] = None" in psrc
        assert "import_uploaded_voice(project_id, narration_upload" in psrc
