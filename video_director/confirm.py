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
        # 数值类编辑（差距 6）：scale（画面缩放）与 duration_delta（时长增减）。
        # 分镜书记录用户意图；真正的 IR 缩放/时长重算在 resume_after_confirm。
        if "scale" in edit and edit["scale"]:
            try:
                sc["scale_hint"] = round(min(max(float(edit["scale"]), 0.2), 3.0), 3)
            except (TypeError, ValueError):
                pass
        if "duration_delta" in edit and edit["duration_delta"]:
            try:
                delta = float(edit["duration_delta"])
                cur = float(sc.get("duration") or 0.0)
                new_dur = min(max(cur + delta, 0.3), 60.0)
                sc["duration"] = round(new_dur, 3)
                for a in sc.get("assets_used", []):
                    if a.get("duration"):
                        a["duration"] = round(
                            max(a["duration"] + delta, 0.3), 3)
            except (TypeError, ValueError):
                pass
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


def parse_edit_instruction_llm(instruction: str, storyboard: dict, llm_call) -> dict:
    """LLM 自然语言改稿 → 结构化 edits（失败/无法解析返回 {}，调用方规则兜底）。

    这是「自然语言真实意义」：用户随意说（换素材/删场景/改文案/重排/
    全素材约束…），LLM 理解意图转成 apply_storyboard_edits 的结构化 edits。
    """
    scenes = storyboard.get("scenes", []) or []
    if not scenes or not instruction or not instruction.strip():
        return {}
    scene_desc = "\n".join(
        f"{sc.get('scene_id')}: \"{sc.get('text','')}\""
        f"（主素材 {[a.get('asset_id') for a in (sc.get('assets_used') or [])][:2]}）"
        for sc in scenes
    )
    # 可用素材池（跨场景去重 asset_id → file）
    asset_pool: dict = {}
    for sc in scenes:
        for a in (sc.get("assets_used") or []):
            aid = a.get("asset_id")
            if aid and aid not in asset_pool:
                import os
                asset_pool[aid] = os.path.basename(a.get("file", "") or "") or aid
    asset_desc = "\n".join(f"{aid}: {fn}" for aid, fn in asset_pool.items())
    prompt = (
        "你是视频分镜改稿助手。用户对分镜书提出自然语言修改，请你输出结构化 edits。\n\n"
        f"分镜书场景：\n{scene_desc}\n\n"
        f"可用素材：\n{asset_desc}\n\n"
        f"用户要求：「{instruction}」\n\n"
        "只输出 JSON（不要 markdown 代码块），格式：\n"
        "{{\"remove\": [\"场景id\"], \"场景id\": {{\"asset\": \"素材id\"}}, "
        "\"场景id\": {{\"text\": \"新文案\"}}, \"reorder\": [\"场景id顺序\"]}}\n"
        "规则：remove 删场景；asset 换素材（从可用素材选）；text 改文案；"
        "reorder 重排（全量顺序）。用户没要求的字段不要输出。"
        "若用户要求无法用这些字段表达，输出空对象 {{}}。"
    )
    try:
        raw = llm_call(prompt)
    except Exception:
        return {}
    if not raw:
        return {}
    # 剥 markdown 代码块，取第一个 { 到最后一个 }
    import re as _re
    text = _re.sub(r"^```[a-zA-Z]*\s*", "", raw.strip())
    text = _re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    # 校验字段合法性（scene_id 必须真实存在；asset 必须来自素材池或保持原样）
    valid_ids = {str(sc.get("scene_id")) for sc in scenes}
    edits: dict = {}
    for k, v in parsed.items():
        if k == "remove":
            ids = [str(x) for x in (v or []) if str(x) in valid_ids]
            if ids:
                edits["remove"] = ids
        elif k == "reorder":
            ids = [str(x) for x in (v or []) if str(x) in valid_ids]
            if ids:
                edits["reorder"] = ids
        elif k in valid_ids and isinstance(v, dict):
            sub = {}
            if v.get("asset"):
                sub["asset"] = str(v["asset"])
            if v.get("text"):
                sub["text"] = str(v["text"])
            if sub:
                edits[k] = sub
    return edits


def parse_edit_instruction(instruction: str, storyboard: dict) -> dict:
    """自然语言改稿 → 结构化 edits（规则兜底，格式与 apply_storyboard_edits 一致）。

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

    # 数值类编辑（差距 6：报告二点名「把产品放大一点」类指令）：
    #   放大/缩小 [N倍] / 加长/延长 N秒 / 缩短 N秒
    # 两步解析：先用动作词定性+取倍数，再用 _resolve_scene_index 定位场景——
    # 避免「第2段的产品放大一点」这类中间插词把单一正则搞炸。
    _ZOOM_M = re.search(r"(放大|缩小|放大一点|缩小一点)"
                        r"(?:\s*([0-9.]+|一|二|两|三|四|五)\s*倍)?", text)
    if _ZOOM_M:
        idx = _resolve_scene_index(text, n)
        if idx is not None:
            word, factor = _ZOOM_M.group(1), _ZOOM_M.group(2)
            if word.startswith("缩"):
                scale = 1.0 / (float(factor) if factor and "." in factor
                               else _CN_NUM.get(factor, 1.25) if factor else 1.25)
            else:
                scale = (float(factor) if factor and "." in factor
                         else _CN_NUM.get(factor, 1.25) if factor else 1.25)
            return {ids[idx]: {"scale": round(min(max(scale, 0.2), 3.0), 3)}}

    m = re.search(r"(?:加长|延长|多放)\s*([0-9.]+)\s*秒", text)
    if m:
        idx = _resolve_scene_index(text, n)
        if idx is not None:
            return {ids[idx]: {"duration_delta": float(m.group(1))}}
    m = re.search(r"(?:缩短|减短|减去)\s*([0-9.]+)\s*秒", text)
    if m:
        idx = _resolve_scene_index(text, n)
        if idx is not None:
            return {ids[idx]: {"duration_delta": -float(m.group(1))}}

    return {}


def merge_edits(base: Optional[dict], delta: Optional[dict]) -> dict:
    """合并两批 edits（多轮改稿累积）。

    remove 取并集去重；reorder 是全量顺序语义，后者整体替换；
    逐场景字段（text/asset）后者覆盖。
    """
    merged: dict = json.loads(json.dumps(base or {}, ensure_ascii=False))
    for k, v in (delta or {}).items():
        if k == "remove":
            cur = list(merged.get(k) or [])
            for x in (v or []):
                if x not in cur:
                    cur.append(x)
            merged[k] = cur
        elif k == "reorder":
            merged[k] = list(v or [])
        else:
            merged[k] = {**(merged.get(k) or {}), **v}
    return merged


def resolve_asset_references(edits: dict, storyboard: dict) -> dict:
    """把 edits 里 asset 值从「文件名/序号」解析为 manifest 真实 asset_id。

    真相源是项目 assets/manifest.json（build_manifest 生成，id=文件名 stem）。
    storyboard 的 assets_used 可能含语义增强的别名 id（如 "v2"），不可信。
    用户自然语言说「换成 3.mp4」或「换成 3」→ 解析成 manifest id。
    无法解析则原样保留（planner 侧会过滤不存在的 id）。
    """
    by_name: Dict[str, str] = {}
    from video_director import config as cfg
    pid = storyboard.get("project_id") if isinstance(storyboard, dict) else None
    manifest_path = None
    if pid:
        manifest_path = cfg.project_dir(pid) / "assets" / "manifest.json"
    if manifest_path is None or not manifest_path.is_file():
        # 无 manifest 可用（防御）：退回 storyboard 自身的 asset_id/file 提示
        for sc in storyboard.get("scenes", []):
            for a in sc.get("assets_used", []) or []:
                aid = a.get("asset_id")
                f = a.get("file", "") or ""
                if not aid or not f:
                    continue
                base = f.replace("\\", "/").split("/")[-1]
                stem = base.rsplit(".", 1)[0] if "." in base else base
                for key in (str(aid), base, stem):
                    if key:
                        by_name[key] = str(aid)
    else:
        import json as _json
        try:
            entries = _json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            entries = []
        for e in entries:
            aid = e.get("id")
            f = e.get("file", "") or ""
            if not aid:
                continue
            base = f.replace("\\", "/").split("/")[-1]
            stem = base.rsplit(".", 1)[0] if "." in base else base
            for key in (str(aid), base, stem):
                if key and key not in by_name:
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


def render_draft(project_id: str, scale: float = DRAFT_SCALE, profile: Optional[str] = None) -> str:
    """读已落盘的 composition.json IR → 缩小分辨率快速渲染 draft.mp4。

    返回 draft 路径。供 WebUI st.video 播放（Gate B 审片）。
    不烧字幕、不做完整 QC——草稿只求快（用户看节奏/素材对不对）。

    profile（§43）：草案也支持 4K profile（target 分辨率从 profile 取，
    缩 scale 后输出）；默认 None 走原 IR 尺寸。
    """
    from video_director import config, pipeline, renderer, film_document as fd
    from video_director.models import CompositionIR, AssetRef
    fd.assert_legacy_composition_access(project_id, "生成旧时间线草稿")

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
    # 已被编辑器写入 IR、但不在项目 manifest 的全局素材，也必须参与预览/导出。
    # 仅信任资产库中的 asset_id→file 映射；不能直接信任 composition.json 中任意路径。
    try:
        from video_director import asset_catalog as asset_catalog_mod
        for ir_asset in ir.assets:
            if ir_asset.id in assets or str(ir_asset.file or "").startswith("audio-library://"):
                continue
            entry = asset_catalog_mod.get_asset(ir_asset.id)
            if not entry or not entry.get("file"):
                continue
            trusted_file = Path(str(entry["file"])).resolve()
            if not trusted_file.is_file():
                continue
            from video_director.models import AssetRef
            assets[ir_asset.id] = AssetRef(
                id=ir_asset.id, type=entry.get("type") or ir_asset.type, file=str(trusted_file),
                duration=entry.get("duration") if entry.get("duration") is not None else ir_asset.duration,
                keywords=list(entry.get("tags") or entry.get("keywords") or ir_asset.keywords or []),
                source=entry.get("source") or ir_asset.source,
                license_note=entry.get("license_note") or ir_asset.license_note,
            )
    except Exception as exc:
        logger.warning(f"draft: IR 全局素材解析失败（继续检查可用资产）: {exc}")

    # 音频库资产并入（IR.audio 引用 bgm/sfx 时渲染器要找文件）
    try:
        from video_director import audio as audio_mod
        assets = audio_mod.append_audio_assets(assets, audio_mod.scan_library())
    except Exception as exc:
        logger.warning(f"draft: 音频库并入失败（不阻塞）: {exc}")

    # §43：profile 决定目标尺寸（4K 诚实：草稿缩放预览，尺寸标注在 profile）
    from video_director import output_profile
    if profile:
        prof = output_profile.resolve_profile(profile, ir.project.width, ir.project.height)
        target_w, target_h = prof.width, prof.height
    else:
        target_w, target_h = ir.project.width, ir.project.height

    # 缩分辨率（草稿 IR 与正式 IR 同构，仅 project 尺寸缩）
    dw = max(2, int(target_w * scale) // 2 * 2)   # 偶数对齐（yuv420p 要求）
    dh = max(2, int(target_h * scale) // 2 * 2)
    draft_ir = ir.model_copy(deep=True)
    object.__setattr__(draft_ir.project, "width", dw)
    object.__setattr__(draft_ir.project, "height", dh)

    narration_audio_path = dirs["audio"] / "narration.wav"
    if not narration_audio_path.is_file():
        narration_audio_path = dirs["audio"] / "narration.mp3"
    narration = str(narration_audio_path) if narration_audio_path.is_file() else None

    render_out = renderer.render_composition(
        draft_ir, assets, dirs["render"] / "draft", narration_file=narration,
        subtitle_file=None,  # 草稿不烧字幕（快）
        project_id=project_id,
        artifact_kind="draft",
    )
    draft_path = str(dirs["render"] / "draft.mp4")
    # render_composition 产物名是 final.mp4（在传入 work_dir 内），改名到 draft.mp4
    import shutil as _sh
    if Path(render_out.final_path) != Path(draft_path):
        _sh.copyfile(render_out.final_path, draft_path)
    logger.info(f"draft rendered: {draft_path} ({dw}x{dh})")
    return draft_path



def _read_intent_duration(base: Path) -> str:
    """从落盘 spec 读回用户目标时长（编辑器导出路径没有内存 spec，
    director_agent 已把它持久化在 plans/director_spec.json）。"""
    try:
        p = base / "plans" / "director_spec.json"
        if p.is_file():
            return str(json.loads(p.read_text(encoding="utf-8")).get("_intent_duration_s") or "")
    except Exception:
        pass
    return ""


def _render_saved_composition_final(project_id: str, profile: Optional[str] = None) -> str:
    """Render the saved editor CompositionIR as the final artifact without re-planning it.

    The editor has already committed its picture/audio clips through revisioned commands.
    Re-running the story planner at export time would overwrite those edits, so this path
    compiles the persisted IR directly through the same deterministic renderer.
    """
    import shutil
    from video_director import asset_catalog, audio as audio_mod, config, prober, qc, renderer, store

    base = config.project_dir(project_id)
    dirs = config.init_project_dirs(base)
    ir_path = dirs["plans"] / "composition.json"
    if not ir_path.is_file():
        raise ConfirmError(f"composition.json 不存在（项目未规划完成）: {ir_path}")
    ir = CompositionIR(**json.loads(ir_path.read_text(encoding="utf-8")))

    # Project manifest remains authoritative for project-local uploads.
    assets: dict = {}
    manifest_path = dirs["assets"] / "manifest.json"
    if manifest_path.is_file():
        for row in json.loads(manifest_path.read_text(encoding="utf-8")):
            ref = AssetRef(**row)
            assets[ref.id] = ref

    # Resolve global catalog references by stable asset ID, never by trusting a
    # filesystem path from the editable Composition JSON.
    for ir_asset in ir.assets:
        if ir_asset.id in assets or str(ir_asset.file or "").startswith("audio-library://"):
            continue
        entry = asset_catalog.get_asset(ir_asset.id)
        if not entry or not entry.get("file"):
            continue
        trusted_file = Path(str(entry["file"])).resolve()
        if not trusted_file.is_file():
            continue
        assets[ir_asset.id] = AssetRef(
            id=ir_asset.id, type=entry.get("type") or ir_asset.type, file=str(trusted_file),
            duration=entry.get("duration") if entry.get("duration") is not None else ir_asset.duration,
            keywords=list(entry.get("tags") or entry.get("keywords") or ir_asset.keywords or []),
            source=entry.get("source") or ir_asset.source,
            license_note=entry.get("license_note") or ir_asset.license_note,
        )
    # Audio manifest is the canonical resolver for audio-library:// IDs; merge
    # it after catalog/manifest assets, matching the draft-render path contract.
    assets = audio_mod.append_audio_assets(assets, audio_mod.scan_library())

    render_ir = ir.model_copy(deep=True)
    if profile:
        from video_director import output_profile
        resolved_profile = output_profile.resolve_profile(
            profile, render_ir.project.width, render_ir.project.height
        )
        object.__setattr__(render_ir.project, "width", resolved_profile.width)
        object.__setattr__(render_ir.project, "height", resolved_profile.height)

    narration_path = dirs["audio"] / "narration.wav"
    if not narration_path.is_file():
        narration_path = dirs["audio"] / "narration.mp3"
    narration = str(narration_path) if narration_path.is_file() else None
    subtitle_path = dirs["render"] / "master.ass"
    # 对抗审查 MEDIUM 8：编辑器改过字幕（set_subtitle_track 写 ir.subtitle_track.cues）
    # 但旧 master.ass 是规划期产物——原样烧录会把用户改的文字/时间点丢掉。
    # cues 非空时从 IR 重建 ass（样式仍由 renderer 读 ir.subtitle_style 控制）。
    if ir.subtitle_track and ir.subtitle_track.cues:
        from video_director.subtitle import ASS_PLAIN_HEADER, _ass_time
        width = int(render_ir.project.width or 1080)
        height = int(render_ir.project.height or 1920)
        font_size = 64
        if ir.subtitle_style is not None:
            try:
                font_size = max(20, int(64 * float(ir.subtitle_style.size or 1.0)))
            except (TypeError, ValueError):
                font_size = 64
        header = ASS_PLAIN_HEADER.format(
            width=width, height=height, font_size=font_size,
            margin_v=max(40, int(height * 0.08)),
        )
        events = []
        for cue in ir.subtitle_track.cues:
            text = str(cue.text or "").strip()
            if not text:
                continue
            disp = text if len(text) <= 16 else text[: len(text) // 2] + "\\N" + text[len(text) // 2:]
            events.append(f"Dialogue: 0,{_ass_time(cue.start)},{_ass_time(cue.end)},Sub,,0,0,0,,{disp}")
        subtitle_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    subtitles = str(subtitle_path) if subtitle_path.is_file() else None
    title_card_path = dirs["render"] / "title_card.mp4"
    title_card = str(title_card_path) if title_card_path.is_file() else None

    # Keep render-only options that older projects stored in the project row
    # rather than CompositionIR. They still apply when exporting hand-edited IR.
    project_row = store.get_project(project_id) or {}
    render_params = {}
    if project_row.get("params_json"):
        try:
            render_params = json.loads(project_row["params_json"])
        except (TypeError, json.JSONDecodeError):
            render_params = {}
    lower_third_name = render_params.get("lower_third_name")
    lower_third_role = render_params.get("lower_third_role")
    legacy_watermark = render_params.get("watermark")

    store.set_stage(project_id, store.Stage.RENDERING)
    work_dir = dirs["render"] / "editor-final"
    # 对抗审查 HIGH 1：渲染失败必须落 FAILED+error，否则项目永久卡 RENDERING。
    # 旧管线 pipeline._compose 有同型 except → _fail(...) 收口，这条新导出路径
    # 之前没有，缺素材时异常穿透、DB 停在「渲染中」60%。
    try:
        rendered = renderer.render_composition(
            render_ir, assets, work_dir, narration_file=narration,
            subtitle_file=subtitles, title_card=title_card,
            lower_third_name=lower_third_name,
            lower_third_role=lower_third_role,
            watermark=legacy_watermark,
            project_id=project_id, artifact_kind="final",
        )
    except Exception as exc:
        store.set_stage(project_id, store.Stage.FAILED, error=f"最终导出渲染失败：{exc}")
        raise
    final_path = dirs["output"] / "final.mp4"
    final_path.parent.mkdir(parents=True, exist_ok=True)
    if Path(rendered.final_path).resolve() != final_path.resolve():
        shutil.copyfile(rendered.final_path, final_path)

    # Final export is not reported as complete unless the deterministic QC gate
    # accepts the exact MP4 that will be shown/downloaded to the user.
    store.set_stage(project_id, store.Stage.QC)
    narration_duration = float(render_ir.project.duration)
    narration_track = next((clip for clip in render_ir.audio if clip.type.value == "narration"), None)
    narration_expected = bool(narration) and not bool(narration_track and narration_track.muted)
    other_audio_expected = any(
        not clip.muted and clip.type.value != "narration"
        for clip in render_ir.audio
    )
    report = qc.run_qc(
        render_ir, str(final_path), assets, narration_duration,
        expect_subtitle=bool(subtitles),
        expect_audio=narration_expected or other_audio_expected,
        intent_duration=_read_intent_duration(base),
    )
    report_data = report.to_dict()
    if not report.ok:
        store.set_stage(project_id, store.Stage.FAILED, error=json.dumps(report_data, ensure_ascii=False))
        raise ConfirmError(f"编辑器最终导出未通过 QC，成片没有被标记为完成：{report_data}")
    store.finish_project(project_id, str(final_path), narration_duration, report_data)
    return str(final_path)

def commercial_release_gate(project_id: str) -> dict:
    """差距 7（仓库审计）：正式渲染前检查素材授权状态。

    BLOCKED（明确禁止商用）→ 硬阻止；UNKNOWN/REVIEW（授权待核实）→ 警告
    但不阻止（Jerry 裁决：当前 stock 素材多为 UNKNOWN，硬阻止会出不了片）。

    返回 {"blocked": [...], "unknown": [...]}。授权状态取 Composition IR 里
    AssetRef.rights_status（由 assets._library_semantics 从 Library 的
    commercial_status 归一而来，USER_SUPPLIED→CLEAR）。
    """
    from video_director import editor
    ir = editor.load_composition(project_id)
    blocked: List[str] = []
    unknown: List[str] = []
    for a in ir.assets:
        rs = str(getattr(a, "rights_status", "") or "").strip().upper()
        if rs == "BLOCKED":
            blocked.append(a.id)
        elif rs in ("UNKNOWN", "REVIEW"):
            unknown.append(a.id)
    return {"blocked": list(dict.fromkeys(blocked)),
            "unknown": list(dict.fromkeys(unknown))}

def finalize_after_review(project_id: str, edits: Optional[dict] = None, profile: Optional[str] = None):
    """最终渲染：不带分镜修改时直接编译编辑器保存的 CompositionIR。

    明确的分镜 edits 仍走旧的规划恢复流程；时间线编辑则必须以保存后的 IR 为
    唯一事实源，不能在导出时重新运行 planner 覆盖手工调整。
    """
    from video_director import film_document as fd
    fd.assert_legacy_composition_access(project_id, "导出旧时间线成片")
    if edits:
        from video_director import pipeline
        return pipeline.resume_after_confirm(project_id, edits=edits, profile=profile)
    return _render_saved_composition_final(project_id, profile=profile)
