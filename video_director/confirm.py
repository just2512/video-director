"""分镜预览书（Storyboard Preview）— 成片前人工确认环节的产物。

用户 2026-09-20 裁决的「成片前人工调整确认环节」核心落地：
- compose_auto 在 PLANNING 完成后暂停，不直接渲染
- 生成分镜书（给用户看，不是内部 IR）→ WebUI/CLI 展示
- 用户确认/改素材/删场景/调顺序 → resume_after_confirm 继续 RENDERING→QC

分镜书是「可读 dict」，字段固定、结构清晰，方便 WebUI 直接 JSON 渲染或
markdown 呈现。与 Composition IR 的关系：IR 是渲染唯一输入，分镜书是
人审唯一输入；确认后若有 edits，先把 edits 应用到分镜书，再依据分镜书
重生成 IR（或直接复用原 IR 当无 edits）。

edits 契约（够用即止，第一版不支持复杂编辑）：
    {
      "S02": {"asset": "v3"},          # 改 S02 的主素材为 v3
      "S05": {"text": "新文案"},        # 改 S05 的旁白文案
      "remove": ["S04"],               # 删除 S04
      "reorder": ["S01", "S03", "S02"]  # 重新排序（可选；省略则保持原顺序）
    }

零外部依赖（stdlib + 包内 models）。不 import pipeline 避免循环。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

from video_director.models import CompositionIR, AssetRef


class ConfirmError(Exception):
    """分镜书构建/编辑过程的结构性失败。"""


def build_storyboard(
    ir: CompositionIR,
    scenes: List[dict],
    assignments: Optional[Dict[str, str]],
    style,
    assets: Dict[str, AssetRef],
) -> dict:
    """从 Composition IR + 场景 + 匹配结果生成分镜预览书。

    返回结构（给用户看，不是内部 IR）：
        {
          "style_id": "...",
          "style_reason": ["...", "..."],
          "total_duration": 12.34,
          "scene_count": 3,
          "scenes": [
            {
              "scene_id": "S01",
              "text": "旁白文案",
              "start": 0.0,
              "duration": 4.12,
              "assets_used": [
                {"asset_id": "v1", "type": "video", "duration": 4.12,
                 "description": "...", "file": "..."}
              ],
              "style_id": "cinematic-story"
            }
          ]
        }

    scenes 参数来自 narration_scenes（含真实时长）。assignments 是
    matcher.match_assets 的结果 {scene_id: asset_id}；None 则按 IR layers
    里的 asset_id 提取。
    style 是 StyleContract（dataclass）；None 则 style_id 留空。
    assets 是 build_manifest 的完整 asset_map（含 description/file 等）。
    """
    if not ir.scenes:
        raise ConfirmError("IR 无场景，无法生成分镜书")
    if not scenes:
        raise ConfirmError("narration_scenes 为空")

    scene_map = {s["id"]: s for s in scenes}
    style_id = ""
    style_reason: List[str] = []
    if style is not None:
        style_id = getattr(style, "style_id", "") or ""
        style_reason = list(getattr(style, "reason", []) or [])

    sb_scenes: List[dict] = []
    for scene_ir in ir.scenes:
        sid = scene_ir.id
        narration = scene_map.get(sid)
        text = (narration or {}).get("text", scene_ir.text or "")
        duration = round(scene_ir.duration, 3)

        # 选取本场景的主素材：优先用 assignments（语义匹配结果），
        # 其次取 IR layers 里第一个带 asset_id 的层
        primary_aid: Optional[str] = None
        if assignments and sid in assignments:
            primary_aid = assignments[sid]
        if primary_aid is None:
            for layer in scene_ir.layers:
                if layer.asset_id:
                    primary_aid = layer.asset_id
                    break

        assets_used: List[dict] = []
        # 主素材
        if primary_aid and primary_aid in assets:
            a = assets[primary_aid]
            assets_used.append(_asset_summary(a, scene_ir.duration))
        # 其他在本场景出现的素材（去重主素材）
        for layer in scene_ir.layers:
            if not layer.asset_id or layer.asset_id == primary_aid:
                continue
            if layer.asset_id in assets:
                a = assets[layer.asset_id]
                if not any(x["asset_id"] == a.id for x in assets_used):
                    assets_used.append(_asset_summary(a, layer.duration))

        sb_scenes.append({
            "scene_id": sid,
            "text": text,
            "start": round(scene_ir.start, 3),
            "duration": duration,
            "assets_used": assets_used,
            "style_id": style_id,
        })

    total = ir.scenes[-1].start + ir.scenes[-1].duration
    return {
        "style_id": style_id,
        "style_reason": style_reason,
        "total_duration": round(total, 3),
        "scene_count": len(sb_scenes),
        "scenes": sb_scenes,
    }


def _asset_summary(a: AssetRef, duration: float) -> dict:
    """单素材摘要（分镜书里给用户看的条目）。"""
    desc = a.description or a.prompt or ""
    if not desc:
        # 降级：文件名 stem 作为画面描述（零依赖语义兜底）
        desc = Path(a.file).stem if a.file else ""
    return {
        "asset_id": a.id,
        "type": a.type,
        "duration": round(duration, 3),
        "description": desc,
        "file": a.file,
    }


def storyboard_to_markdown(sb: dict) -> str:
    """分镜书渲染成 markdown（WebUI st.markdown / CLI print 共用）。

    结构：
        # 分镜预览书
        **风格**: cinematic-story · 总时长 12.34s · 3 场景

        ---

        ## S01 · 4.12s
        > 旁白文案内容...

        - [video] v1 · 4.12s · 战场出征
        - [image] img1 · 2.00s · 古墓

        ---
    """
    if not sb or "scenes" not in sb:
        raise ConfirmError("分镜书结构非法（缺 scenes 字段）")

    style_id = sb.get("style_id", "")
    style_reason = sb.get("style_reason", []) or []
    total = sb.get("total_duration", 0.0)
    n = sb.get("scene_count", 0)
    scenes = sb.get("scenes", [])

    lines: List[str] = []
    lines.append("# 分镜预览书")
    lines.append("")
    style_str = style_id or "（未选风格）"
    lines.append(f"**风格**: {style_str} · 总时长 {total:.2f}s · {n} 场景")
    if style_reason:
        lines.append(f"**风格理由**: {'；'.join(style_reason)}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for sc in scenes:
        sid = sc.get("scene_id", "?")
        dur = sc.get("duration", 0.0)
        text = sc.get("text", "")
        assets_used = sc.get("assets_used", []) or []
        lines.append(f"## {sid} · {dur:.2f}s")
        lines.append("")
        if text:
            # 引用块呈现旁白文案
            for ln in text.split("\n"):
                if ln.strip():
                    lines.append(f"> {ln.strip()}")
                    lines.append("")
        if assets_used:
            for a in assets_used:
                atype = a.get("type", "?")
                aid = a.get("asset_id", "?")
                adur = a.get("duration", 0.0)
                desc = a.get("description", "") or "（无描述）"
                lines.append(f"- [{atype}] {aid} · {adur:.2f}s · {desc}")
        else:
            lines.append("- （无素材）")
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def apply_storyboard_edits(sb: dict, edits: dict) -> dict:
    """把用户编辑回写到分镜书，返回新分镜书（不 mutate 原对象）。

    edits 契约（够用即止）：
        {
          "S02": {"asset": "v3"},          # 改 S02 主素材 asset_id
          "S05": {"text": "新文案"},        # 改 S05 旁白文案
          "remove": ["S04"],               # 删除 S04
          "reorder": ["S01", "S03", "S02"]  # 重新排序（可选）
        }

    - 改素材：在 scenes[].assets_used 里把主素材替换为指定 asset_id；
      若新 asset_id 不在原 assets_used 列表，则追加。
      注意：本函数只改分镜书展示，真正的 IR 重规划在 resume_after_confirm。
    - 改文案：直接替换 scenes[].text。
    - 删除场景：从 scenes 列表移除。
    - 重排序：按 reorder 列表重排 scenes（缺失的场景追加在末尾）。

    返回新 dict（保持原 sb 不变）。edits 为空则原样返回新副本。
    """
    if not isinstance(sb, dict):
        raise ConfirmError(f"分镜书类型非法: {type(sb).__name__}")
    if not isinstance(edits, dict):
        raise ConfirmError(f"edits 类型非法: {type(edits).__name__}")

    # 深拷贝（保持原对象不变）
    new_sb = json.loads(json.dumps(sb, ensure_ascii=False))
    scenes = new_sb.get("scenes", [])
    by_id = {sc["scene_id"]: sc for sc in scenes}

    # 1. 删除场景
    remove_ids = edits.get("remove") or []
    if not isinstance(remove_ids, list):
        raise ConfirmError("edits.remove 必须是列表")
    for rid in remove_ids:
        by_id.pop(rid, None)
    new_scenes = [sc for sc in scenes if sc["scene_id"] not in set(remove_ids)]

    # 2. 改文案 / 改素材
    for sc in new_scenes:
        sid = sc["scene_id"]
        edit = edits.get(sid)
        if not edit:
            continue
        if "text" in edit and edit["text"]:
            sc["text"] = str(edit["text"])
        if "asset" in edit and edit["asset"]:
            new_aid = str(edit["asset"])
            # 在 assets_used 中找已存在条目替换，否则追加
            found = False
            for a in sc.get("assets_used", []):
                if a.get("asset_id") == new_aid:
                    found = True
                    break
            # 简化：清空原主素材列表，仅保留用户指定的新主素材（描述用占位）
            # 真正的 IR 重规划在 resume_after_confirm 里做，分镜书只是视觉提示
            if not found:
                sc["assets_used"] = [{
                    "asset_id": new_aid,
                    "type": "video",  # 类型待 resume 时查 assets_map 确定
                    "duration": sc.get("duration", 0.0),
                    "description": f"（用户指定替换：{new_aid}）",
                    "file": "",
                }]
            else:
                # 把指定素材挪到第一位
                kept = [a for a in sc.get("assets_used", []) if a.get("asset_id") == new_aid]
                rest = [a for a in sc.get("assets_used", []) if a.get("asset_id") != new_aid]
                sc["assets_used"] = kept + rest

    # 3. 重排序
    reorder = edits.get("reorder")
    if reorder:
        if not isinstance(reorder, list):
            raise ConfirmError("edits.reorder 必须是列表")
        id_order = {sid: i for i, sid in enumerate(reorder)}
        # 保持未列出的场景追加在末尾（原相对顺序）
        remainder = [sc for sc in new_scenes if sc["scene_id"] not in id_order]
        listed = [sc for sc in new_scenes if sc["scene_id"] in id_order]
        listed.sort(key=lambda sc: id_order[sc["scene_id"]])
        new_scenes = listed + remainder

    # 重新计算 start / total_duration（删除或重排后时间轴要修正）
    cursor = 0.0
    for sc in new_scenes:
        sc["start"] = round(cursor, 3)
        cursor += sc.get("duration", 0.0)
    new_sb["scenes"] = new_scenes
    new_sb["scene_count"] = len(new_scenes)
    new_sb["total_duration"] = round(cursor, 3)
    return new_sb


# ---------------------------------------------------------------------------
# 自然语言改稿解析（方案 §54/§27-29：NL → Revision Patch；复杂意图上层接 LLM）
# ---------------------------------------------------------------------------
_CN_NUM = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _resolve_scene_index(instruction: str, n_scenes: int) -> Optional[int]:
    """从「第N段 / 第N个 / 最后一段」解析 0-based 场景序号；失败返回 None。"""
    if n_scenes <= 0:
        return None
    if ("最后" in instruction) or ("末尾" in instruction):
        return n_scenes - 1
    m = re.search(r"第\s*(\d+)\s*[段个镜]", instruction)
    if m:
        v = int(m.group(1))
        return v - 1 if 1 <= v <= n_scenes else None
    m = re.search(r"第\s*([零一两二三四五六七八九十]+)\s*[段个镜]", instruction)
    if m:
        cn = m.group(1)
        if cn == "十":
            v = 10
        elif "十" in cn:
            parts = cn.split("十")
            tens = 1 if parts[0] == "" else _CN_NUM.get(parts[0], 1)
            ones = _CN_NUM.get(parts[1], 0)
            v = tens * 10 + ones
        else:
            v = _CN_NUM.get(cn, 0)
        return v - 1 if 1 <= v <= n_scenes else None
    return None


def parse_edit_instruction(instruction: str, storyboard: dict) -> dict:
    """自然语言改稿 → 结构化 edits（格式与 apply_storyboard_edits 一致）。

    支持（规则匹配）：
      - 删掉/删除/去掉/移除 第N段            → {"remove": ["S0N"]}
      - 第N段 换成/替换成 X（素材）          → {"S0N": {"asset": "X"}}
      - 第N段 改成/文案改成 X               → {"S0N": {"text": "X"}}
    无法识别 → 返回 {}（上层提示「未能识别的修改」，请换一种说法）。
    """
    scenes = storyboard.get("scenes", []) or []
    n = len(scenes)
    if n == 0 or not instruction or not instruction.strip():
        return {}
    ids = [str(sc.get("scene_id", "")) for sc in scenes]
    text = instruction.strip()

    if re.search(r"(删掉|删除|去掉|移除)", text):
        idx = _resolve_scene_index(text, n)
        if idx is not None and 0 <= idx < n:
            return {"remove": [ids[idx]]}

    m = re.search(r"(?:换成|替换成|换为|素材换成|素材改为)\s*([^\s，,。；;]+)", text)
    if m:
        idx = _resolve_scene_index(text, n)
        if idx is not None:
            return {ids[idx]: {"asset": m.group(1)}}

    m = re.search(r"(?:改成|文案改成|旁白改成|改为)\s*(.+)$", text)
    if m and m.group(1).strip():
        idx = _resolve_scene_index(text, n)
        if idx is not None:
            return {ids[idx]: {"text": m.group(1).strip()}}

    return {}


def merge_edits(base: Optional[dict], delta: Optional[dict]) -> dict:
    """合并两批 edits（多轮改稿累积）。remove/reorder 取并集去重，逐场景字段后者覆盖。"""
    merged: dict = json.loads(json.dumps(base or {}, ensure_ascii=False))
    for k, v in (delta or {}).items():
        if k in ("remove", "reorder"):
            cur = list(merged.get(k) or [])
            for x in (v or []):
                if x not in cur:
                    cur.append(x)
            merged[k] = cur
        else:
            merged[k] = {**(merged.get(k) or {}), **v}
    return merged


def resolve_asset_references(edits: dict, storyboard: dict) -> dict:
    """把 edits 里 asset 值从「文件名/序号」解析为 storyboard 的 asset_id。

    用户自然语言说「换成 3.mp4」或「换成 3」或「换成 v3」，统一解析成 manifest
    的 asset_id（assets_used 里既存 asset_id 又有 file）。无法解析则原样保留。
    """
    by_name: Dict[str, str] = {}
    for sc in storyboard.get("scenes", []):
        for a in sc.get("assets_used", []) or []:
            aid = a.get("asset_id")
            f = a.get("file", "") or ""
            if not aid:
                continue
            base = f.split("/")[-1] if "/" in f else f
            stem = base.rsplit(".", 1)[0] if "." in base else base
            for key in (str(aid), base, stem):
                if key:
                    by_name[key] = str(aid)
    out = json.loads(json.dumps(edits, ensure_ascii=False))
    for edit in out.values():
        if isinstance(edit, dict) and edit.get("asset"):
            tok = str(edit["asset"])
            if tok in by_name:
                edit["asset"] = by_name[tok]
    return out


def save_storyboard(sb: dict, path: Path) -> None:
    """分镜书落盘（plans/storyboard.json）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(sb, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(f"storyboard saved: {path} ({sb.get('scene_count', 0)} scenes)")


def load_storyboard(path: Path) -> dict:
    """从 plans/storyboard.json 读回分镜书。文件不存在抛 ConfirmError。"""
    if not path.is_file():
        raise ConfirmError(f"分镜书文件不存在: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ConfirmError(f"分镜书解析失败: {exc}") from exc
    if not isinstance(data, dict) or "scenes" not in data:
        raise ConfirmError("分镜书结构非法（缺 scenes 字段）")
    return data


# ---------------------------------------------------------------------------
# Gate B 动态草稿（ShotcraftAI 方案 §十一：确认应分两级——Gate A 分镜书
# 已有；Gate B 让用户"看完整视频"再改。低分辨率快速渲染 draft.mp4，
# 不烧字幕（快），用户播完 → finalize_after_review 走正式渲染。
# ---------------------------------------------------------------------------

#: 草稿分辨率缩放（0.5 → 1080x1920 变 540x960）
DRAFT_SCALE = 0.5


def render_draft(project_id: str, scale: float = DRAFT_SCALE) -> str:
    """读已落盘的 composition.json IR → 缩小分辨率快速渲染 draft.mp4。

    返回 draft 路径。供 WebUI st.video 播放（Gate B 审片）。
    不烧字幕、不做完整 QC——草稿只求快（用户看节奏/素材对不对）。
    """
    from video_director import config, pipeline, renderer
    from video_director.models import CompositionIR, AssetRef

    base = config.project_dir(project_id)
    dirs = config.init_project_dirs(base)
    ir_path = dirs["plans"] / "composition.json"
    if not ir_path.is_file():
        raise ConfirmError(f"composition.json 不存在（项目未规划完成）: {ir_path}")
    ir = CompositionIR(**json.loads(ir_path.read_text(encoding="utf-8")))

    # 资产表读回
    manifest_path = dirs["assets"] / "manifest.json"
    assets: dict = {}
    if manifest_path.is_file():
        for a in json.loads(manifest_path.read_text(encoding="utf-8")):
            ref = AssetRef(**a)
            assets[ref.id] = ref
    # 音频库资产并入（IR.audio 引用 bgm/sfx 时渲染器要找文件）
    try:
        from video_director import audio as audio_mod
        assets = audio_mod.append_audio_assets(assets, audio_mod.scan_library())
    except Exception as exc:
        logger.warning(f"draft: 音频库并入失败（不阻塞）: {exc}")

    # 缩分辨率（草稿 IR 与正式 IR 同构，仅 project 尺寸缩）
    dw = max(2, int(ir.project.width * scale) // 2 * 2)   # 偶数对齐（yuv420p 要求）
    dh = max(2, int(ir.project.height * scale) // 2 * 2)
    draft_ir = ir.model_copy(deep=True)
    object.__setattr__(draft_ir.project, "width", dw)
    object.__setattr__(draft_ir.project, "height", dh)

    narration_audio_path = dirs["audio"] / "narration.wav"
    narration = str(narration_audio_path) if narration_audio_path.is_file() else None

    render_out = renderer.render_composition(
        draft_ir, assets, dirs["render"] / "draft", narration_file=narration,
        subtitle_file=None,  # 草稿不烧字幕（快）
    )
    draft_path = str(dirs["render"] / "draft.mp4")
    # render_composition 产物名是 final.mp4（在传入 work_dir 内），改名到 draft.mp4
    import shutil as _sh
    if Path(render_out.final_path) != Path(draft_path):
        _sh.copyfile(render_out.final_path, draft_path)
    logger.info(f"draft rendered: {draft_path} ({dw}x{dh})")
    return draft_path


def finalize_after_review(project_id: str, edits: Optional[dict] = None):
    """Gate B 审片通过 → 正式渲染（复用 pipeline.resume_after_confirm）。

    返回 PipelineResult（final.mp4 + QC）。
    """
    from video_director import pipeline
    return pipeline.resume_after_confirm(project_id, edits=edits)
