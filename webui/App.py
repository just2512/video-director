"""AI Video Director WebUI（独立入口，不属于任何外部项目）。

用户只需：粘贴文案 + 拖素材 +（可选）风格描述 + 点「自动制作」。
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from video_director import audio as vd_audio  # noqa: E402
from video_director import pipeline  # noqa: E402


EDGE_ZH_VOICES = [
    "zh-CN-XiaoxiaoNeural",   # 晓晓（女）
    "zh-CN-YunxiNeural",      # 云希（男）
    "zh-CN-YunyangNeural",    # 云扬（男，新闻腔）
    "zh-CN-YunjianNeural",    # 云健（男，沉稳）
    "zh-CN-liaoning-XiaobeiNeural",  # 晓北（东北）
]


def _save_uploads(uploaded, target_dir: Path) -> list:
    paths = []
    for uf in uploaded:
        dest = target_dir / uf.name
        dest.write_bytes(uf.getvalue())
        paths.append(str(dest))
    return paths


def _ensure_audio_library():
    try:
        vd_audio.generate_test_audio_library()
    except Exception:
        pass


def main():
    st.set_page_config(page_title="AI Video Director", page_icon="🎬", layout="wide")
    st.title("🎬 AI Video Director")
    st.caption("一句话文案 + 素材 → AI 自动导演：理解内容、匹配画面、配音、配乐、成片")

    _ensure_audio_library()
    lib = vd_audio.scan_library()
    bgm_options = ["（关闭）"] + [a.id for a in lib.values() if a.type == "bgm"]

    col1, col2 = st.columns([2, 1])
    with col1:
        script = st.text_area(
            "文案", height=280,
            placeholder="粘贴你的完整文案……",
        )
    with col2:
        uploaded_videos = st.file_uploader(
            "视频素材（可多选）", type=["mp4", "mov", "mkv", "webm"],
            accept_multiple_files=True,
        )
        uploaded_images = st.file_uploader(
            "图片素材（可多选）", type=["jpg", "jpeg", "png", "webp"],
            accept_multiple_files=True,
        )
        uploaded_semantic = st.file_uploader(
            "素材语义标注（可选 assets_semantic.json，用于按内容匹配画面）",
            type=["json"], accept_multiple_files=False,
        )
        voice = st.selectbox("配音", EDGE_ZH_VOICES, index=0)
        style_intent = st.text_input(
            "风格（可选，留空 AI 自动决定）",
            placeholder="例：历史悬疑感 / 快节奏有冲击力 / 克制的知识纪录片",
        )
        bgm_choice = st.selectbox("背景音乐", bgm_options, index=0)
        st.caption("画幅：9:16 竖屏")

    if st.button("🎬 自动制作", type="primary", use_container_width=True):
        if not script.strip():
            st.error("请先粘贴文案")
            st.stop()
        if not uploaded_videos and not uploaded_images:
            st.error("请至少拖入一个视频或图片素材")
            st.stop()

        project_id = f"web-{uuid.uuid4().hex[:8]}"
        project_dir = ROOT / "storage" / project_id
        videos_dir = project_dir / "input" / "videos"
        images_dir = project_dir / "input" / "images"
        videos_dir.mkdir(parents=True, exist_ok=True)
        images_dir.mkdir(parents=True, exist_ok=True)

        video_paths = _save_uploads(uploaded_videos, videos_dir) if uploaded_videos else []
        image_paths = _save_uploads(uploaded_images, images_dir) if uploaded_images else []

        manual_manifest = None
        if uploaded_semantic is not None:
            sem_path = project_dir / "input" / "assets_semantic.json"
            sem_path.write_bytes(uploaded_semantic.getvalue())
            manual_manifest = str(sem_path)

        start = time.time()
        try:
            with st.status("AI 正在理解内容并生成视频……", expanded=True) as status:
                status.write("正在配音……")
                result = pipeline.compose_auto(
                    script=script,
                    video_files=video_paths,
                    image_files=image_paths,
                    project_id=project_id,
                    voice=voice,
                    manual_manifest=manual_manifest,
                    style_intent=(style_intent.strip() or None) if style_intent else None,
                    bgm_asset_id=(bgm_choice if bgm_choice != "（关闭）" else None),
                )
                status.write(f"✅ 完成，耗时 {time.time() - start:.1f}s")
            st.session_state["vd_result"] = result
        except Exception as exc:
            st.error(f"生成失败：{exc}")
            st.stop()

    result = st.session_state.get("vd_result")
    if result:
        st.success(
            f"生成成功 · 旁白 {result.narration_duration:.1f}s · "
            f"QC {result.qc_report['verdict']}"
        )
        st.video(result.final_path)
        with open(result.final_path, "rb") as f:
            st.download_button("⬇️ 下载 MP4", f.read(), "final.mp4", "video/mp4")
        with st.expander("Composition IR（时间轴）"):
            st.json(result.ir)
        with st.expander("QC 报告"):
            st.json(result.qc_report)
        if result.warnings:
            st.warning("提示：" + "；".join(result.warnings))


main()