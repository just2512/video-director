"""参考视频分析层（v1.2 §13-§14/§32-§35）。

核心思想（§14 风格重构，不是内容复制）：
    reference.mp4 → 分析视觉语法 → 提取镜头语言 → 用自己的素材重新实现
    - 不复制原片画面/品牌/文字/素材
    - 不让 LLM「看完整视频总结」（§33 反模式）
    - 正路径：PySceneDetect 语义 → 每镜头抽代表帧 → Contact Sheet → Vision LLM → Shot Grammar

本模块实现 §33 正路径的确定性预处理部分，不引外部依赖：
    1. shot_detector       ffmpeg scene 滤镜（select+showinfo）检测镜头边界
    2. frame_sampler       每镜头抽代表帧（ffmpeg -ss 中点 -frames:v 1）
    3. extract_shot_grammar 镜头时长/节奏 → 结构化（motion 可选 LLM 标注，
                            无 LLM 时启发式推断并标 source='heuristic'）
    4. reference_analyzer  主入口，产 reference_report.json（§34 格式）

不 import pipeline（避免循环）；LLM 可选（llm_provider.get_llm() 有则用，无则兜底）。
所有子产物落盘到 out_dir/reference/（§52 reference/ 收敛）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from video_director import prober
from video_director.llm_provider import get_llm


class ReferenceError(Exception):
    """参考视频分析结构性失败（无法探测/无法抽帧/无镜头）。"""


# ---------------------------------------------------------------------------
# 常量（§34 格式 + §33 阈值）
# ---------------------------------------------------------------------------

#: scene 检测阈值（ffmpeg scene 分数 0..1，0.3 为常见经验值）
#: 小于此值视为同镜头内的微动而非切镜
SCENE_THRESHOLD = 0.3

#: 每镜头默认抽帧数（代表帧）
FRAMES_PER_SHOT_DEFAULT = 1

#: 镜头最短时长（秒）：低于此视为噪声碎片，合并入前一镜头
MIN_SHOT_DURATION_S = 0.2

#: pace 分级阈值（§34：由镜头平均时长推断）
PACE_FAST_THRESHOLD = 1.5  # < 1.5s = fast
PACE_SLOW_THRESHOLD = 4.0  # > 4.0s = slow

#: 抽帧宽度（缩放控制 token/带宽，与 vision.extract_frames 对齐）
FRAME_WIDTH = 480

#: 输出子目录（§52 收敛）
REFERENCE_SUBDIR = "reference"


# ---------------------------------------------------------------------------
# 数据契约（§34 visual_language + shot_grammar）
# ---------------------------------------------------------------------------


@dataclass
class Shot:
    """单个镜头边界。

    start_s/end_s/duration_s 为绝对秒；scene_score 保留检测器原始分数。
    """

    index: int
    start_s: float
    end_s: float
    duration_s: float
    scene_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ShotGrammar:
    """单个镜头的结构化语言（§34 shot_grammar 项）。

    role 取值：opening/development/turning_point/climax/transition/closing/unknown
    motion 取值：static/slow_pan/slow_zoom/quick_cut/fast_motion/unknown
    source 取值：heuristic（启发式推断）/ llm（LLM 分析帧后标注）
    """

    index: int
    role: str
    duration_s: float
    motion: str
    source: str = "heuristic"


# ---------------------------------------------------------------------------
# 1. shot_detector：镜头边界检测（§33 确定性预处理）
# ---------------------------------------------------------------------------


def shot_detector(video_path: str, threshold: float = SCENE_THRESHOLD) -> List[Shot]:
    """检测视频的镜头边界。

    用 ffmpeg 的 scene 检测滤镜
    ``-vf "select='gt(scene,{threshold})',showinfo"`` 解析 showinfo 的 pts_time
    作为每个新镜头的起点。比 PySceneDetect 更轻量（零外部依赖）。

    Args:
        video_path: 参考视频文件路径
        threshold: scene 分数阈值 0..1（默认 0.3）

    Returns:
        List[Shot]：每个镜头含 start_s/end_s/duration_s/scene_score

    Raises:
        ReferenceError: 视频不可探测 / 无视频流 / 无法解析边界
    """
    if not os.path.isfile(video_path):
        raise ReferenceError(f"参考视频不存在: {video_path}")

    ffmpeg = prober._resolve_ffmpeg()

    # 总时长（用于最后一个镜头的 end_s）
    probe = prober.probe_media(video_path)
    if probe.duration is None or probe.duration <= 0:
        raise ReferenceError(f"参考视频无有效时长: {video_path}")
    total_dur = probe.duration

    # ffmpeg scene 检测：select+showinfo 输出被选中帧的 pts_time
    cmd = [
        ffmpeg, "-hide_banner", "-i", video_path,
        "-filter_complex", f"select='gt(scene,{threshold})',showinfo",
        "-f", "null", "-",
    ]
    try:
        completed = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReferenceError(f"ffmpeg scene 检测超时: {video_path}") from exc

    # showinfo 在 stderr 输出 pts_time: 3 等
    # scene 切换帧即新镜头的第一帧
    boundary_times: List[float] = []
    pattern = re.compile(r"pts_time:(?P<t>\d+(?:\.\d+)?)")
    for line in (completed.stderr or "").splitlines():
        if "showinfo" not in line:
            continue
        m = pattern.search(line)
        if not m:
            continue
        try:
            t = float(m.group("t"))
        except ValueError:
            continue
        if 0 <= t <= total_dur:
            boundary_times.append(t)

    if not boundary_times:
        # 无切镜：整片是一个镜头
        return [Shot(index=0, start_s=0.0, end_s=total_dur,
                     duration_s=round(total_dur, 3), scene_score=0.0)]

    # 去重 + 排序（相邻重复值合并）
    boundary_times = sorted(set(boundary_times))

    # 构造镜头列表：第一个镜头从 0 开始
    shots: List[Shot] = []
    prev_start = 0.0
    for i, bt in enumerate(boundary_times):
        if bt - prev_start < MIN_SHOT_DURATION_S:
            # 太短，视为噪声，合并入前一镜头（不单独成镜）
            prev_start = bt
            continue
        shots.append(Shot(
            index=len(shots),
            start_s=round(prev_start, 3),
            end_s=round(bt, 3),
            duration_s=round(bt - prev_start, 3),
            scene_score=0.0,  # select 模式不返回 score；sc_score 字段保留
        ))
        prev_start = bt

    # 收尾：最后一段到 total_dur
    if total_dur - prev_start > MIN_SHOT_DURATION_S:
        shots.append(Shot(
            index=len(shots),
            start_s=round(prev_start, 3),
            end_s=round(total_dur, 3),
            duration_s=round(total_dur - prev_start, 3),
            scene_score=0.0,
        ))
    else:
        # 末段太短，并入前一镜头
        if shots:
            last = shots[-1]
            shots[-1] = Shot(
                index=last.index,
                start_s=last.start_s,
                end_s=round(total_dur, 3),
                duration_s=round(total_dur - last.start_s, 3),
                scene_score=last.scene_score,
            )

    if not shots:
        raise ReferenceError(
            f"scene 检测失败（无有效镜头，threshold={threshold}）: {video_path}"
        )
    return shots


# ---------------------------------------------------------------------------
# 2. frame_sampler：每镜头抽代表帧
# ---------------------------------------------------------------------------


def frame_sampler(
    video_path: str,
    shots: List[Shot],
    out_dir: Path,
    frames_per_shot: int = FRAMES_PER_SHOT_DEFAULT,
) -> List[str]:
    """为每个镜头抽代表帧（jpg）。

    抽样点：每个镜头在 [start, end] 内均匀分布。
    frames_per_shot=1 时取中点，>1 时取等分点。

    Args:
        video_path: 参考视频路径
        shots: shot_detector 返回的镜头列表
        out_dir: 帧输出目录（自动创建）
        frames_per_shot: 每镜头抽帧数（默认 1）

    Returns:
        List[str]：所有帧 jpg 路径（顺序与 shots 对齐；>1 帧/镜头时按
        镜头顺序连续排列）

    Raises:
        ReferenceError: 全部抽帧失败
    """
    if not os.path.isfile(video_path):
        raise ReferenceError(f"参考视频不存在: {video_path}")
    if frames_per_shot < 1:
        raise ValueError(f"frames_per_shot 必须 >= 1，实际 {frames_per_shot}")

    out_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = prober._resolve_ffmpeg()
    stem = Path(video_path).stem
    paths: List[str] = []

    for shot in shots:
        for j in range(frames_per_shot):
            # 等分点：j+0.5 形式取中点（避免落到首尾 fade/黑帧）
            frac = (j + 0.5) / frames_per_shot
            t = shot.start_s + shot.duration_s * frac
            # 边界保护
            t = max(0.0, min(t, shot.end_s - 0.04 if shot.end_s > 0.04 else shot.end_s))
            suffix = f"shot{shot.index:03d}_f{j + 1}" if frames_per_shot > 1 else f"shot{shot.index:03d}"
            out_file = str(out_dir / f"{stem}_{suffix}.jpg")
            r = subprocess.run(
                [ffmpeg, "-y", "-loglevel", "error",
                 "-ss", f"{t:.3f}", "-i", video_path,
                 "-frames:v", "1",
                 "-vf", f"scale={FRAME_WIDTH}:-2",
                 out_file],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60,
            )
            if r.returncode != 0:
                logger.warning(
                    f"抽帧失败 {video_path}@{t:.1f}s shot#{shot.index}: "
                    f"{(r.stderr or '')[-200:]}"
                )
                continue
            if Path(out_file).is_file():
                paths.append(out_file)
            else:
                logger.warning(f"抽帧声称成功但文件不存在: {out_file}")

    if not paths:
        raise ReferenceError(f"全部抽帧失败: {video_path}")
    return paths


# ---------------------------------------------------------------------------
# 3. extract_shot_grammar：镜头语言结构化
# ---------------------------------------------------------------------------


def extract_shot_grammar(
    shots: List[Shot],
    motion_hints: Optional[List[Dict[str, Any]]] = None,
) -> List[ShotGrammar]:
    """把镜头时长/节奏转结构化 shot_grammar。

    LLM 可用时尝试用 get_llm() 分析帧（motion_hints 由调用方传入，
    通常是 reference_analyzer 把帧路径 + LLM prompt 的结果转回这里）；
    无 LLM 时用确定性启发式（时长短→快切，时长长→慢镜）并标 source='heuristic'。

    role 分配（启发式）：
        - 第 1 个镜头 → opening
        - 最后 1 个镜头 → closing
        - 中间最短的那个（节奏突快）→ turning_point
        - 其余 → development

    motion 分配（启发式）：
        - duration < 1.0s → quick_cut
        - duration < 2.0s → fast_motion
        - duration > 5.0s → slow_pan（启发式无法区分 pan/zoom，笼统 slow）
        - 其余 → static

    Args:
        shots: shot_detector 返回的镜头列表
        motion_hints: 可选，每镜头的 LLM 分析结果
            [{index, motion, role, source='llm'}]

    Returns:
        List[ShotGrammar]：与 shots 等长
    """
    if not shots:
        return []

    # 启发式 role 分配
    n = len(shots)
    role_map: Dict[int, str] = {}
    if n == 1:
        role_map[shots[0].index] = "opening"
    else:
        role_map[shots[0].index] = "opening"
        role_map[shots[-1].index] = "closing"
        if n >= 3:
            # 中间最短的标 turning_point（节奏突快处通常承担转折）
            mid = shots[1:-1]
            shortest_mid = min(mid, key=lambda s: s.duration_s)
            role_map[shortest_mid.index] = "turning_point"
            for s in shots[1:-1]:
                if s.index not in role_map:
                    role_map[s.index] = "development"
        # n==2 只有 opening/closing

    # 启发式 motion 分配
    def _heuristic_motion(dur: float) -> str:
        if dur < 1.0:
            return "quick_cut"
        if dur < 2.0:
            return "fast_motion"
        if dur > 5.0:
            return "slow_pan"
        return "static"

    grammars: List[ShotGrammar] = []
    hint_by_index: Dict[int, Dict[str, Any]] = {}
    if motion_hints:
        for h in motion_hints:
            idx = h.get("index")
            if idx is not None:
                hint_by_index[idx] = h

    for shot in shots:
        hint = hint_by_index.get(shot.index)
        if hint and hint.get("motion"):
            grammars.append(ShotGrammar(
                index=shot.index,
                role=str(hint.get("role") or role_map.get(shot.index, "unknown")),
                duration_s=shot.duration_s,
                motion=str(hint.get("motion")),
                source=str(hint.get("source", "llm")),
            ))
        else:
            grammars.append(ShotGrammar(
                index=shot.index,
                role=role_map.get(shot.index, "unknown"),
                duration_s=shot.duration_s,
                motion=_heuristic_motion(shot.duration_s),
                source="heuristic",
            ))
    return grammars


# ---------------------------------------------------------------------------
# 4. reference_analyzer：主入口
# ---------------------------------------------------------------------------


def _infer_pace(shots: List[Shot]) -> str:
    """由镜头平均时长推断 pace（§34）。"""
    if not shots:
        return "medium"
    avg = sum(s.duration_s for s in shots) / len(shots)
    if avg < PACE_FAST_THRESHOLD:
        return "fast"
    if avg > PACE_SLOW_THRESHOLD:
        return "slow"
    return "medium"


def _infer_density(shots: List[Shot], total_dur: float) -> str:
    """由镜头密度（镜头/分钟）推断 density（§34）。

    <6/min=sparse, 6-15/min=medium, >15/min=dense
    """
    if total_dur <= 0 or not shots:
        return "medium"
    per_min = len(shots) / total_dur * 60
    if per_min < 6:
        return "sparse"
    if per_min > 15:
        return "dense"
    return "medium"


def _aggregate_motion(grammars: List[ShotGrammar]) -> str:
    """由 shot_grammar 聚合出 visual_language.motion 主值。"""
    if not grammars:
        return "static"
    # 取出现次数最多的 motion（平局时取首个）
    counts: Dict[str, int] = {}
    for g in grammars:
        counts[g.motion] = counts.get(g.motion, 0) + 1
    # 优先级：quick_cut > fast_motion > slow_pan > static（快节奏优先）
    priority = ["quick_cut", "fast_motion", "slow_pan", "slow_zoom", "static"]
    for m in priority:
        if m in counts:
            return m
    return grammars[0].motion


def _aggregate_transition(grammars: List[ShotGrammar]) -> str:
    """转场类型聚合（启发式：默认 mostly_cut，无 dissolve 信息来源）。"""
    # ffmpeg scene 检测只给硬切，默认 mostly_cut
    # 若 LLM 在 motion_hints 标注了 transition，未来可在此扩展
    return "mostly_cut"


def _try_llm_motion(
    frame_paths: List[str],
    shots: List[Shot],
) -> Optional[List[Dict[str, Any]]]:
    """尝试用 LLM 分析帧序列得到 motion_hints。

    无 LLM（get_llm() is None）或调用失败时返回 None，调用方走启发式。
    """
    llm = get_llm()
    if llm is None:
        return None
    if not frame_paths:
        return None

    # 帧数对齐到镜头数（frame_sampler 默认 1 帧/镜头）
    # 若 frames_per_shot > 1，这里只取每镜头第一帧做 LLM 分析
    aligned_frames: List[str] = []
    idx = 0
    for _ in shots:
        if idx < len(frame_paths):
            aligned_frames.append(frame_paths[idx])
            idx += 1
        else:
            break
    if len(aligned_frames) != len(shots):
        # 抽帧数与镜头数不匹配，无法对齐，跳过 LLM
        logger.warning(
            f"LLM motion 分析跳过：抽帧数 {len(aligned_frames)} != 镜头数 {len(shots)}"
        )
        return None

    # 构造 LLM prompt：把帧作为文字描述喂给 LLM（不喂 base64，避免 LLM Provider
    # 非 vision 模型）。让 LLM 根据时序描述推断 motion 与 role。
    # 注意：本系统 LLM Provider 是 OpenAI 兼容文本模型（不一定支持 vision）。
    # 真正的 Vision LLM 分析走 vision.py 的 FrameVisionProvider，reference 层
    # 只把帧路径列表 + 时长信息转成文字描述让 LLM 推断。
    prompt_parts: List[str] = []
    prompt_parts.append(
        "你是视频镜头分析师。下面是一个参考视频的镜头序列（每镜头抽一帧）。"
        "请根据帧画面变化与时序，推断每个镜头的 motion（取值："
        "static/slow_pan/slow_zoom/quick_cut/fast_motion）与 role（取值："
        "opening/development/turning_point/climax/transition/closing）。"
        "只输出 JSON 数组，不要任何解释或 markdown 代码块：\n"
        '[{"index":0,"motion":"...","role":"..."}, ...]'
    )
    for i, (shot, fp) in enumerate(zip(shots, aligned_frames)):
        prompt_parts.append(
            f"镜头#{shot.index} 时长{shot.duration_s:.2f}s 帧={Path(fp).name}"
        )
    prompt = "\n".join(prompt_parts)

    try:
        raw = llm.generate(prompt)
    except Exception as exc:
        logger.warning(f"LLM motion 分析失败，走启发式: {exc}")
        return None
    if not raw:
        return None

    # 宽容解析（复用 llm_provider._parse_storyboard 的剥壳思路）
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        items = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(items, list):
        return None

    hints: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        idx_val = item.get("index")
        motion = item.get("motion")
        role = item.get("role")
        if idx_val is None or not motion:
            continue
        hints.append({
            "index": int(idx_val),
            "motion": str(motion),
            "role": str(role) if role else None,
            "source": "llm",
        })
    if not hints:
        return None
    return hints


def reference_analyzer(
    video_path: str,
    out_dir: str | Path,
    *,
    threshold: float = SCENE_THRESHOLD,
    frames_per_shot: int = FRAMES_PER_SHOT_DEFAULT,
    use_llm: bool = True,
) -> Dict[str, Any]:
    """分析参考视频 → reference_report.json（§34 格式）。

    流程：
        1. shot_detector 检测镜头边界
        2. frame_sampler 每镜头抽代表帧
        3. 可选：LLM 分析帧序列得 motion_hints
        4. extract_shot_grammar 聚合结构化镜头语言
        5. 由 shot_grammar 推断 visual_language（pace/density/motion/transition）
        6. report + 帧落盘到 out_dir/reference/

    Args:
        video_path: 参考视频路径
        out_dir: 产物根目录（自动创建 reference/ 子目录）
        threshold: scene 检测阈值
        frames_per_shot: 每镜头抽帧数
        use_llm: 是否尝试用 LLM 标注（无 LLM 时自动降级启发式）

    Returns:
        dict（§34 格式）:
            {
              "video": str,
              "visual_language": {"pace","density","typography","motion","transition"},
              "shot_grammar": [{"role","duration","motion"}],
              "audio_language": {"music","sfx"},
              "frames": [str],
              "report_path": str
            }

    Raises:
        ReferenceError: 分析失败（无镜头/抽帧失败）
    """
    if not os.path.isfile(video_path):
        raise ReferenceError(f"参考视频不存在: {video_path}")

    base = Path(out_dir) / REFERENCE_SUBDIR
    base.mkdir(parents=True, exist_ok=True)
    frame_dir = base / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)

    # 1. 镜头检测
    shots = shot_detector(video_path, threshold=threshold)
    logger.info(f"shot_detector: {len(shots)} 个镜头 "
                f"(threshold={threshold}, video={Path(video_path).name})")

    # 2. 抽帧
    frame_paths = frame_sampler(
        video_path, shots, frame_dir, frames_per_shot=frames_per_shot,
    )
    logger.info(f"frame_sampler: {len(frame_paths)} 帧抽出到 {frame_dir}")

    # 3. LLM motion 标注（可选）
    motion_hints: Optional[List[Dict[str, Any]]] = None
    if use_llm:
        motion_hints = _try_llm_motion(frame_paths, shots)
        if motion_hints is None:
            logger.info("LLM 不可用或失败，motion 走启发式（source='heuristic'）")
        else:
            logger.info(f"LLM motion 标注: {len(motion_hints)} 条")

    # 4. shot_grammar
    grammars = extract_shot_grammar(shots, motion_hints=motion_hints)

    # 5. visual_language 聚合
    probe = prober.probe_media(video_path)
    total_dur = probe.duration or 0.0
    pace = _infer_pace(shots)
    density = _infer_density(shots, total_dur)
    motion_main = _aggregate_motion(grammars)
    transition = _aggregate_transition(grammars)

    # typography 默认 unknown（启发式无法从 scene 检测推出字体信息，
    # 需要 Vision LLM 看帧才能推断，本版不实现）
    # audio_language 默认空（参考视频音频转写/分类留到后续 vision 链路）
    # video_fingerprint：源视频内容哈希（审计差距 3：导演链用它判定报告
    # 是否对应当前视频；流式读避免大文件整载）。
    import hashlib as _hashlib
    _digest = _hashlib.sha256()
    with open(video_path, "rb") as _vf:
        for _chunk in iter(lambda: _vf.read(1024 * 1024), b""):
            _digest.update(_chunk)
    report: Dict[str, Any] = {
        "video": os.path.abspath(video_path),
        "video_fingerprint": _digest.hexdigest(),
        "duration_s": round(total_dur, 3),
        "shot_count": len(shots),
        "visual_language": {
            "pace": pace,
            "density": density,
            "typography": "unknown",
            "motion": motion_main,
            "transition": transition,
        },
        "shot_grammar": [
            {
                "index": g.index,
                "role": g.role,
                "duration": g.duration_s,
                "motion": g.motion,
                "source": g.source,
            }
            for g in grammars
        ],
        "audio_language": {
            "music": "unknown",
            "sfx": "unknown",
        },
        "frames": frame_paths,
        "report_path": "",
    }

    # 6. 落盘
    report_file = base / "reference_report.json"
    report_to_write = dict(report)
    report_to_write["report_path"] = str(report_file)
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report_to_write, f, ensure_ascii=False, indent=2)
    logger.info(f"reference_report 落盘: {report_file}")

    report["report_path"] = str(report_file)
    return report


__all__ = [
    "Shot",
    "ShotGrammar",
    "ReferenceError",
    "shot_detector",
    "frame_sampler",
    "extract_shot_grammar",
    "reference_analyzer",
    "SCENE_THRESHOLD",
    "FRAMES_PER_SHOT_DEFAULT",
    "PACE_FAST_THRESHOLD",
    "PACE_SLOW_THRESHOLD",
    "REFERENCE_SUBDIR",
]
