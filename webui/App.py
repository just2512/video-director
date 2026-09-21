"""AI Video Director WebUI（独立入口，不属于任何外部项目）。

用户只需：粘贴文案 + 拖素材 +（可选）风格描述 + 点「自动制作」。

2026-09-20 新增确认模式：默认勾选「先预览分镜再渲染」——
compose_auto(auto_confirm=False) 规划完成后暂停，展示分镜书，
用户点「确认渲染」→ resume_after_confirm 继续。
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
from video_director import store as vd_store  # noqa: E402


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


def _render_storyboard_panel(sb: dict) -> None:
    """在 Streamlit 页面渲染分镜书。"""
    st.subheader("分镜预览书")
    style_id = sb.get("style_id", "（未选风格）")
    total = sb.get("total_duration", 0.0)
    n = sb.get("scene_count", 0)
    st.caption(f"风格: {style_id} · 总时长 {total:.1f}s · {n} 场景")

    reason = sb.get("style_reason") or []
    if reason:
        st.info("风格理由：" + "；".join(reason))

    with st.expander("分镜表（Markdown）", expanded=True):
        from video_director.confirm import storyboard_to_markdown
        st.markdown(storyboard_to_markdown(sb))

    with st.expander("分镜表（JSON，供高级编辑参考）", expanded=False):
        st.json(sb)


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
        st.caption("提示：文件名带语义（如「战场出征.mp4」）即可自动按内容匹配，无需任何标注")
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
        # 默认勾选：用户要求「成片前要有人工确认」
        confirm_mode = st.checkbox(
            "先预览分镜再渲染", value=True,
            help="勾选后规划完成暂停，展示分镜书让你确认/修改后再渲染",
        )

    # ------------------------------------------------------------------
    # 主制作按钮
    # ------------------------------------------------------------------
    if st.button("🎬 自动制作", type="primary", use_container_width=True):
        if not script.strip():
            st.error("请先粘贴文案")
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
                if video_paths or image_paths:
                    status.write("正在配音与匹配画面……")
                    result = pipeline.compose_auto(
                        script=script,
                        video_files=video_paths,
                        image_files=image_paths,
                        project_id=project_id,
                        voice=voice,
                        manual_manifest=manual_manifest,
                        style_intent=(style_intent.strip() or None) if style_intent else None,
                        bgm_asset_id=(bgm_choice if bgm_choice != "（关闭）" else None),
                        # confirm_mode=True（要预览）→ auto_confirm=False（暂停）；
                        # 语义相反：auto_confirm 是"跳过确认"，checkbox 是"要确认"
                        auto_confirm=not confirm_mode,
                    )
                else:
                    # 纯文案模式：无素材 → 文字卡 + 旁白 + 字幕（兑现「素材可选」）
                    # 注意：script-only 模式暂不接入确认环节（零素材，分镜书意义低）
                    from video_director import script_only
                    status.write("正在配音与制作文字卡……")
                    so = script_only.compose_script_only(
                        script=script,
                        project_id=project_id,
                        voice=voice,
                        style_intent=(style_intent.strip() or None) if style_intent else None,
                    )
                    result = pipeline.PipelineResult(
                        project_dir=so.project_dir,
                        final_path=so.final_path,
                        ir=so.ir,
                        qc_report=so.qc_report,
                        narration_duration=so.narration_duration,
                        warnings=so.warnings,
                    )
                status.write(f"✅ 完成，耗时 {time.time() - start:.1f}s")
            if result.pending_confirm:
                # 待确认：存到 session，让用户看分镜书
                st.session_state["vd_pending"] = result
                st.session_state["vd_result"] = None
                st.rerun()
            else:
                st.session_state["vd_result"] = result
                st.session_state["vd_pending"] = None
        except Exception as exc:
            st.error(f"生成失败：{exc}")
            st.stop()

    # ------------------------------------------------------------------
    # 确认环节：展示分镜书 + 确认/返回修改按钮
    # ------------------------------------------------------------------
    pending = st.session_state.get("vd_pending")
    if pending:
        st.divider()
        _render_storyboard_panel(pending.storyboard)

        st.warning(
            "已暂停在「等待确认」状态。建议先生成动态草稿看实际效果，"
            "或直接确认渲染，或返回修改文案/素材后重新生成。"
        )

        # ---- Gate B：动态草稿（低分辨率快速版，播放审片） ----
        with st.expander("▶️ 动态草稿预览（低分辨率快速版，看节奏与素材分配）"):
            draft_path = st.session_state.get("vd_draft_path")
            gen_col, _ = st.columns([1, 2])
            with gen_col:
                if st.button("🎬 生成动态草稿", use_container_width=True):
                    pid = pending.project_id or ""
                    try:
                        with st.spinner("正在快速渲染草稿……"):
                            from video_director.confirm import render_draft
                            draft_path = render_draft(pid)
                            st.session_state["vd_draft_path"] = draft_path
                        st.rerun()
                    except Exception as exc:
                        st.error(f"草稿渲染失败：{exc}")
            if draft_path and Path(draft_path).is_file():
                st.video(draft_path)
                st.caption(
                    f"草稿：{Path(draft_path).name}（低分辨率、无字幕、快速版；"
                    "确认渲染后的正式版为全高清+字幕）"
                )
            elif draft_path:
                st.info("草稿文件已被清理，请重新生成。")

        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("✅ 确认渲染", type="primary", use_container_width=True):
                # 确认 → resume_after_confirm
                pid = pending.project_id or ""
                try:
                    with st.status("正在渲染……", expanded=True) as status:
                        resumed = pipeline.resume_after_confirm(pid, edits=None)
                        status.write("✅ 渲染完成")
                    st.session_state["vd_result"] = resumed
                    st.session_state["vd_pending"] = None
                    st.rerun()
                except Exception as exc:
                    st.error(f"渲染失败：{exc}")
                    st.stop()
        with col_b:
            if st.button("↩️ 返回修改", use_container_width=True):
                # 拒绝：confirm_project(approved=False) 回 CREATED
                # 用户改文案/素材后重新点「自动制作」
                try:
                    vd_store.confirm_project(
                        pending.project_id, approved=False, edits=None
                    )
                except Exception:
                    pass
                st.info("已回退。你可以修改文案/素材后重新点「自动制作」。")
                st.session_state["vd_pending"] = None
                st.rerun()

        st.stop()  # 暂停在这里，不显示 result 区块

    # ------------------------------------------------------------------
    # 结果展示
    # ------------------------------------------------------------------
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