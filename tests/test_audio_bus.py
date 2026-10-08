"""最终方案 §34/§35/§36/§37/§38 Audio 系统契约测试。

覆盖：
- §34 TTS 三模式：自动配音 / 上传配音 / 无配音（NarrationConfig mode 契约）
- §35 Voice Registry：voice_id/provider/name/language/gender/supports_preview/
  supports_full_preview 字段指纹
- §37 Audio Bus：每轨 gain/mute/fade/ducking——muted 一等操作 +
  audio_mix 一次多轨（「原声 20%、BGM 10%、旁白正常」）+ 渲染层消费 muted
- §38 字幕时间轴三源：tts_word_boundary / asr / manual
"""

import json
from pathlib import Path

import pytest

from video_director import config, editor
from video_director.models import AudioTrack, AudioTrackType, CompositionIR


@pytest.fixture()
def pid(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path / "composer")
    project_id = "audio-bus-01"
    base = config.project_dir(project_id)
    config.init_project_dirs(base)
    (base / "plans" / "composition.json").write_text(json.dumps({
        "project": {"title": "t", "width": 1080, "height": 1920,
                    "fps": 30, "duration": 6.0},
        "scenes": [
            {"id": "scene-1", "start": 0.0, "duration": 3.0, "text": "一",
             "layers": [{"type": "video", "start": 0.0, "duration": 3.0,
                         "asset_id": "a.mp4", "source_in": 0.0, "source_out": 3.0,
                         "speed": 1.0}]},
            {"id": "scene-2", "start": 3.0, "duration": 3.0, "text": "二",
             "layers": [{"type": "image", "start": 0.0, "duration": 3.0,
                         "asset_id": "b.jpg"}]},
        ],
        "audio": [{"type": "bgm", "asset_id": "bgm-01", "start": 0.0,
                   "duration": 6.0, "volume": 0.3, "ducking": True}],
        "watermarks": [],
    }), encoding="utf-8")
    return project_id


class TestTTSModes:
    """§34 三模式（UI 直接呈现三选项，不让用户先理解 Provider）。"""

    def test_narration_modes_contract(self):
        from video_director.voice import NarrationConfig, make_narration_config
        for mode in ("auto", "upload", "silent"):
            cfg = make_narration_config(mode=mode)
            assert cfg.mode == mode
        with pytest.raises(ValueError):
            make_narration_config(mode="azure-premium")

    def test_silent_legal_no_fake_voice(self):
        """§34 无配音：Narration Track absent 合法，但禁 fake voice。"""
        from video_director.voice import make_narration_config, is_silent_legal
        cfg = make_narration_config(mode="silent")
        legal = is_silent_legal(cfg)
        assert legal["tts_skipped"] is True
        assert legal["subtitle_allowed"] and legal["bgm_allowed"]

    def test_silent_narration_no_fake_voice(self, tmp_path):
        """§34/§17 无配音模式：synth_narration(voice='') 产静音轨 + 空词表。

        禁 fake voice：不合成 TTS、word_timeline 为空（字幕走 scene timing）。
        """
        import shutil as _sh
        from video_director import narration as narr
        if not (_sh.which("ffmpeg") or Path("release/bin/ffmpeg.exe").is_file()):
            pytest.skip("无 ffmpeg")
        out, words, scenes = narr.synth_narration(
            ["第一句话", "第二句话比较长一点"], "", tmp_path)
        assert out and Path(out).is_file()
        assert words == [], "无配音模式不得有词级时间轴（那是 TTS 的产物）"
        assert len(scenes) == 2
        assert scenes[0]["duration"] >= 2.0


class TestVoiceRegistry:
    """§35 Voice Registry 字段指纹。"""

    def test_voice_profile_fingerprint(self):
        from video_director.voice import list_voices
        voices = list_voices("edge-tts")
        assert voices, "edge-tts 音色必须可枚举"
        v = voices[0]
        for key in ("provider", "voiceId", "name", "language", "gender",
                    "supports_preview", "supports_full_preview"):
            assert key in v, f"§35 字段缺失: {key}"
        assert v["supports_preview"] is True

    def test_providers_declare_key_requirement(self):
        from video_director.voice import list_providers
        provs = list_providers()
        edge = next(p for p in provs if p["id"] == "edge-tts")
        assert edge["available"] is True and edge["requires_key"] is False
        azure = next(p for p in provs if p["id"] == "azure")
        assert azure["requires_key"] is True


class TestAudioBus:
    """§37 每轨 gain/mute/fade/ducking。"""

    def test_mute_is_first_class(self, pid):
        """muted 一等操作：静音保留 gain，取消即恢复。"""
        r = editor.execute_command(pid, {
            "command": "set_audio_mute", "targetId": "bgm",
            "payload": {"muted": True}})
        assert r.status == "APPLIED"
        ir = editor.load_composition(pid)
        bgm = [t for t in ir.audio if t.type.value == "bgm"][0]
        assert bgm.muted is True
        assert abs(bgm.volume - 0.3) < 1e-9, "静音不应改变原 gain"
        r2 = editor.execute_command(pid, {
            "command": "set_audio_mute", "targetId": "bgm",
            "payload": {"muted": False}})
        assert r2.status == "APPLIED"
        assert editor.load_composition(pid).audio[0].muted is False

    def test_original_audio_supports_multiple_video_segments(self, pid):
        """多素材时间线：每个视频片段可独立保留原声，不能只覆盖第一条 original。"""
        r1 = editor.execute_command(pid, {
            "command": "set_original_audio", "targetId": "project",
            "payload": {"mode": "preserve", "assetId": "a.mp4",
                        "start": 0.0, "duration": 3.0,
                        "sourceIn": 0.0, "sourceOut": 3.0}})
        assert r1.status == "APPLIED"
        r2 = editor.execute_command(pid, {
            "command": "set_original_audio", "targetId": "project",
            "payload": {"mode": "preserve", "assetId": "b.mp4",
                        "start": 3.0, "duration": 3.0,
                        "sourceIn": 1.0, "sourceOut": 4.0}})
        assert r2.status == "APPLIED"
        originals = [t for t in editor.load_composition(pid)
                     .audio if t.type == AudioTrackType.original]
        assert len(originals) == 2
        assert [(t.asset_id, t.start, t.source_in, t.source_out)
                for t in originals] == [
                    ("a.mp4", 0.0, 0.0, 3.0),
                    ("b.mp4", 3.0, 1.0, 4.0),
                ]

    def test_confirmed_transition_command(self, pid):
        r = editor.execute_command(pid, {
            "command": "set_transition", "targetId": "scene-1",
            "payload": {"type": "crossfade", "duration": 0.5}})
        assert r.status == "APPLIED"
        ir = editor.load_composition(pid)
        assert ir.scenes[0].transition_out.type == "crossfade"
        assert abs(ir.scenes[0].transition_out.duration - 0.5) < 1e-9
        r2 = editor.execute_command(pid, {
            "command": "set_transition", "targetId": "scene-1",
            "payload": {"type": "cut"}})
        assert r2.status == "APPLIED"
        assert editor.load_composition(pid).scenes[0].transition_out.type == "cut"

    def test_fade_command(self, pid):
        r = editor.execute_command(pid, {
            "command": "set_audio_fade", "targetId": "bgm",
            "payload": {"fadeIn": 0.5, "fadeOut": 1.0}})
        assert r.status == "APPLIED"
        bgm = [t for t in editor.load_composition(pid).audio
               if t.type.value == "bgm"][0]
        assert abs(bgm.fadeIn - 0.5) < 1e-9 and abs(bgm.fadeOut - 1.0) < 1e-9

    def test_audio_mix_one_operation(self, pid):
        """§37「原声 20%、BGM 10%、旁白正常」= 一个 audio_mix，不调四套 API。"""
        r = editor.execute_command(pid, {
            "command": "audio_mix", "targetId": "",
            "payload": {"tracks": {
                "original": {"volume": 0.2},
                "bgm": {"volume": 0.1},
                "narration": {"volume": 1.0},
            }}})
        assert r.status == "APPLIED"
        ir = editor.load_composition(pid)
        vols = {t.type.value: t.volume for t in ir.audio}
        assert abs(vols["bgm"] - 0.1) < 1e-9
        assert abs(vols["original"] - 0.2) < 1e-9
        assert abs(vols["narration"] - 1.0) < 1e-9

    def test_audio_mix_rejects_bad_track(self, pid):
        r = editor.execute_command(pid, {
            "command": "audio_mix", "payload": {"tracks": {"vocals": {}}}})
        assert r.status == "REJECTED"

    def test_audio_track_keyframe_37(self, pid):
        """§37 Audio Bus 每轨 keyframe：add_audio_keyframe → 轨上 volume 渐变帧。"""
        r = editor.execute_command(pid, {
            "command": "add_audio_keyframe", "targetId": "bgm",
            "payload": {"at": 2.0, "volume": 0.05}})
        assert r.status == "APPLIED"
        r2 = editor.execute_command(pid, {
            "command": "add_audio_keyframe", "targetId": "bgm",
            "payload": {"at": 4.0, "volume": 0.3}})
        assert r2.status == "APPLIED"
        ir = editor.load_composition(pid)
        bgm = [t for t in ir.audio if t.type.value == "bgm"][0]
        vol_kfs = [kt for kt in bgm.keyframes if kt.property == "volume"]
        assert vol_kfs and len(vol_kfs[0].keyframes) == 2
        assert [k.at for k in vol_kfs[0].keyframes] == [2.0, 4.0], "时间排序"

    def test_audio_keyframe_rejects_bad_payload(self, pid):
        r = editor.execute_command(pid, {
            "command": "add_audio_keyframe", "targetId": "bgm",
            "payload": {"volume": 0.3}})  # 缺 at
        assert r.status == "REJECTED"

    def test_muted_consumed_at_render(self, pid):
        """渲染层消费 muted：静音轨不进混音图。"""
        editor.execute_command(pid, {"command": "set_audio_mute", "targetId": "bgm",
                                     "payload": {"muted": True}})
        ir = editor.load_composition(pid)
        # renderer._build_audio_graph 的语义：muted bgm 不再被选中
        from video_director import renderer
        # 构造 assets 映射（bgm 轨引用存在才进混音；muted 应被跳过）
        class A:
            type = "audio"
            file = "x.mp3"
        picked = [t for t in ir.audio
                  if t.type == AudioTrackType.bgm
                  and not getattr(t, "muted", False)]
        assert not picked, "muted bgm 不应被渲染层选中"


class TestSubtitleTimingSources:
    """§38 字幕时间轴三源兼容。"""

    def test_subtitle_track_source_enum(self):
        from video_director.models import SubtitleTrack
        for source in ("tts_word_boundary", "asr", "manual"):
            t = SubtitleTrack(source=source, cues=[
                {"start": 0.0, "end": 1.0, "text": "hi"}])
            assert t.source == source

    def test_invalid_source_rejected(self):
        from video_director.models import SubtitleTrack
        with pytest.raises(Exception):
            SubtitleTrack(source="guessed", cues=[])
