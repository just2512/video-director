"""Timeline Planner（V1 §14-§17 + V2 §11-§13：CompositionIR 产物）。

两个 Planner：
- RulePlanner：确定性规则（Gate 1 主路径，零外部依赖）
- LLMPlanner：Provider 化（Gate 3），无可用 LLM 时调用方回退 RulePlanner

变速双层策略（V2 §12）：
    required = source_available / target
    0.90 <= required <= 1.50  → 直接变速（preferred）
    1.50 < required <= 1.70   → 仍可变速（absolute 内），但优先考虑裁剪
    required > 1.70           → 1.70x + 裁剪（禁止极端加速）
    required < 0.90           → 不慢放：视频按 0.90x 播完，剩余时长交补时逻辑
                                （V1 Case 03/04：8s→10s/15s 期待 video+image）

补时优先级（V1 §16）：
    同场景视频 → 相邻场景视频 → 场景对应图片 → 相邻图片 → （最后才允许重复素材）
"""

from __future__ import annotations

import json
import re
from typing import Callable, Dict, List, Optional
import random

from loguru import logger

from video_director import config, registry
from video_director.models import (
    AssetRef,
    AudioTrack,
    AudioTrackType,
    CompositionIR,
    Layer,
    LayerType,
    MissingAssetError,
    ProjectSpec,
    SceneIR,
    Selection,
    TransitionOut,
)

PlannerLLM = Callable[[str], str]


class ContentMatchPlanner:
    """内容匹配规划器：场景 ↔ 素材语义匹配驱动时间轴（用户 2026-09-18 裁决的核心能力）。

    与 RulePlanner 的差异只在"素材选择"：RulePlanner 按顺序分配（画面重复无剪辑
    价值的批评由此而来），本 Planner 按 matcher.assignments 的语义匹配结果选素材。
    变速/补时/MISSING 逻辑复用 RulePlanner（同一套确定性算法）。
    """

    def __init__(self, assignments: Dict[str, str]):
        """assignments: {scene_id: asset_id}，来自 matcher.match_assets。"""
        self._assignments = assignments

    def plan(
        self,
        scenes: List[dict],
        assets: Dict[str, AssetRef],
        asset_order: List[str],
        width: int = config.DEFAULT_RESOLUTION[0],
        height: int = config.DEFAULT_RESOLUTION[1],
        fps: int = config.DEFAULT_FPS,
        image_motion: bool = True,
        fit_mode: str = "cover",
        clip_duration: Optional[float] = None,
        clip_speed: Optional[float] = None,
        concat_mode: str = "sequential",
    ) -> CompositionIR:
        rule = RulePlanner()
        # 按匹配结果重排 asset_order：每个场景的主素材 = 匹配的素材。
        # 防御：assignment 值必须真实存在于 assets（None/幽灵 id 一律回退
        # 原 asset_order 顺序，不进 matched_order，否则 RulePlanner KeyError）。
        matched_order: List[str] = []
        used = set()
        fallback_pool = list(asset_order)
        for s in scenes:
            aid = self._assignments.get(s["id"])
            if aid and aid in assets:
                matched_order.append(aid)
                used.add(aid)
            elif fallback_pool:
                matched_order.append(fallback_pool.pop(0))
            # else: 无素材可分（极端情况），交给后续 rest 补位
        # 未被任何场景匹配的素材排在末尾（补时候选池）
        rest = [aid for aid in asset_order if aid not in set(matched_order)]
        return rule.plan(
            scenes=scenes,
            assets=assets,
            asset_order=matched_order + rest,
            width=width, height=height, fps=fps,
            image_motion=image_motion, fit_mode=fit_mode,
            clip_duration=clip_duration, clip_speed=clip_speed,
            concat_mode=concat_mode,
            preferred_asset_ids=self._assignments,
        )


class RulePlanner:
    """确定性规划器：素材按顺序对应场景，变速/补时按固定规则。"""

    def plan(
        self,
        scenes: List[dict],  # [{id, text, start, duration}]（旁白时间轴场景）
        assets: Dict[str, AssetRef],
        asset_order: List[str],
        width: int = config.DEFAULT_RESOLUTION[0],
        height: int = config.DEFAULT_RESOLUTION[1],
        fps: int = config.DEFAULT_FPS,
        image_motion: bool = True,
        fit_mode: str = "cover",
        clip_duration: Optional[float] = None,
        clip_speed: Optional[float] = None,
        concat_mode: str = "sequential",
        preferred_asset_ids: Optional[Dict[str, str]] = None,
    ) -> CompositionIR:
        preferred_asset_ids = preferred_asset_ids or {}
        pool = {aid: 0.0 for aid in asset_order}  # 视频已消费源时长
        if fit_mode not in ("cover", "contain"):
            fit_mode = "cover"
        if concat_mode == "random":
            asset_order = list(asset_order)
            random.Random(len(asset_order) * 1009 + len(scenes) * 917).shuffle(asset_order)
        scene_irs: List[SceneIR] = []

        # 素材全利用（用户诉求：上传 N 个素材全部进时间线）：
        # 视频素材数 > 场景数时，场景内用多镜头均分，让每个素材至少出场一次。
        video_ids = [aid for aid in asset_order if aid in assets and assets[aid].type == "video"]
        _use_cover_all = len(video_ids) > len(scenes) and len(scenes) > 0 and not clip_duration
        _used_videos: set = set()
        _next_video = 0

        for si, scene in enumerate(scenes):
            target = round(float(scene["duration"]), 3)
            if _use_cover_all:
                # 每场景镜头数：均匀分配多出的素材（前几个场景各多 1 个）
                per = len(video_ids) // len(scenes)
                extra = len(video_ids) % len(scenes)
                cnt = per + (1 if si < extra else 0)
                my_videos: List[str] = []
                # 第一优先：当前场景的语义匹配主素材。
                preferred = preferred_asset_ids.get(scene["id"])
                if preferred in video_ids and preferred not in _used_videos:
                    my_videos.append(preferred)
                    _used_videos.add(preferred)
                # 第二优先：剩余素材按稳定顺序补齐镜头数，确保全部素材最终进时间线。
                while len(my_videos) < cnt:
                    chosen = None
                    for _ in range(len(video_ids)):
                        cand = video_ids[_next_video % len(video_ids)]
                        _next_video += 1
                        if cand not in _used_videos:
                            chosen = cand
                            break
                    if chosen is None:
                        chosen = video_ids[si % len(video_ids)]
                    my_videos.append(chosen)
                    _used_videos.add(chosen)
                layers = self._plan_multi_assets(
                    scene_id=scene["id"], target_duration=target,
                    asset_ids=my_videos, assets=assets, pool=pool,
                    image_motion=image_motion,
                )
            else:
                # 前瞻：本场景候选池中是否存在可用图片（决定慢放侧策略）
                candidates_preview = self._candidates(scene_index=si, asset_order=asset_order)
                has_image = any(
                    assets[c].type == "image" for c in candidates_preview if c in assets
                )
                layers = self._plan_scene(
                    scene_id=scene["id"],
                    target_duration=target,
                    assets=assets,
                    pool=pool,
                    asset_order=asset_order,
                    scene_index=si,
                    image_motion=image_motion,
                    has_image_fallback=has_image,
                    fit_mode=fit_mode,
                    clip_duration=clip_duration,
                    clip_speed=clip_speed,
                )
            scene_irs.append(SceneIR(
                id=scene["id"],
                start=float(scene["start"]),
                duration=target,
                text=scene["text"],
                layers=layers,
            ))

        total = scenes[-1]["start"] + scenes[-1]["duration"]
        return CompositionIR(
            project=ProjectSpec(width=width, height=height, fps=fps, duration=round(total, 3)),
            scenes=scene_irs,
            audio=[],  # 旁白音轨由 narration 模块负责追加
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _candidates(scene_index: int, asset_order: List[str]) -> List[str]:
        """场景素材候选（V1 §16 优先级：场景对应 → 相邻 ±1/±2）。"""
        candidates: List[str] = []
        if scene_index < len(asset_order):
            candidates.append(asset_order[scene_index])
        for offset in (1, -1, 2, -2):
            ni = scene_index + offset
            if 0 <= ni < len(asset_order) and asset_order[ni] not in candidates:
                candidates.append(asset_order[ni])
        return candidates

    def _plan_scene(
        self,
        scene_id: str,
        target_duration: float,
        assets: Dict[str, AssetRef],
        pool: Dict[str, float],
        asset_order: List[str],
        scene_index: int,
        image_motion: bool,
        has_image_fallback: bool = False,
        fit_mode: str = "cover",
        clip_duration: Optional[float] = None,
        clip_speed: Optional[float] = None,
    ) -> List[Layer]:
        layers: List[Layer] = []
        remaining = target_duration

        for aid in self._candidates(scene_index, asset_order):
            if remaining <= config.ITEM_DURATION_TOLERANCE:
                break
            asset = assets[aid]
            segment_target = min(remaining, float(clip_duration)) if clip_duration and clip_duration > 0 else remaining
            if asset.type == "video":
                available = (asset.duration or 0.0) - pool[aid]
                if available <= 0.2:  # 残段太短不用
                    continue
                take, speed, out_dur = self._fit_video(
                    available, segment_target, image_fallback=has_image_fallback,
                    forced_speed=clip_speed
                )
                layers.append(Layer(
                    type=LayerType.video,
                    asset_id=aid,
                    start=round(target_duration - remaining, 3),
                    duration=out_dur,
                    speed=speed,
                    source_in=round(pool[aid], 3),
                    source_out=round(pool[aid] + take, 3),
                    fit_mode=fit_mode,
                ))
                pool[aid] += take
                remaining = round(remaining - out_dur, 3)
            else:
                if remaining < config.IMAGE_MIN_DURATION:
                    continue  # 残余太短，图片会闪帧
                motion = None
                if image_motion and remaining >= config.IMAGE_MOTION_MIN_DURATION:
                    motion = "slow_zoom_in"
                layers.append(Layer(
                    type=LayerType.image,
                    asset_id=aid,
                    start=round(target_duration - remaining, 3),
                    duration=round(remaining, 3),
                    motion=motion,
                    fit_mode=fit_mode,
                ))
                remaining = 0.0
                break

        # 素材循环复用（V1 §16 优先级最末档：全部候选耗尽且场景仍有缺口时，
        # 从资产清单头重新轮转。视频耗尽则游标归零从头复用（同一素材多次
        # 出现是合法的"重复素材"档），图片重新挂载。素材数 < 场景数是真实
        # 产品常态，循环是正确行为而非编造素材。）
        if remaining > config.ITEM_DURATION_TOLERANCE:
            for round_i in range(len(asset_order) * 2):  # 两轮上限防死循环
                if remaining <= config.ITEM_DURATION_TOLERANCE:
                    break
                aid = asset_order[round_i % len(asset_order)]
                asset = assets[aid]
                if asset.type == "video":
                    if pool[aid] >= (asset.duration or 0.0) - 0.2:
                        pool[aid] = 0.0  # 已耗尽 → 游标归零，从头复用
                    avail = (asset.duration or 0.0) - pool[aid]
                    if avail <= 0.2:
                        continue
                    take, speed, out_dur = self._fit_video(
                        avail, remaining, image_fallback=has_image_fallback
                    )
                    layers.append(Layer(
                        type=LayerType.video,
                        asset_id=aid,
                        start=round(target_duration - remaining, 3),
                        duration=out_dur,
                        speed=speed,
                        source_in=round(pool[aid], 3),
                        source_out=round(pool[aid] + take, 3),
                    ))
                    pool[aid] += take
                    remaining = round(remaining - out_dur, 3)
                else:
                    if remaining < config.IMAGE_MIN_DURATION:
                        continue
                    motion = "slow_zoom_in" if (
                        image_motion and remaining >= config.IMAGE_MOTION_MIN_DURATION
                    ) else None
                    layers.append(Layer(
                        type=LayerType.image,
                        asset_id=aid,
                        start=round(target_duration - remaining, 3),
                        duration=round(remaining, 3),
                        motion=motion,
                    ))
                    remaining = 0.0
                    break

        if remaining > config.ITEM_DURATION_TOLERANCE:
            raise MissingAssetError(
                scene_id=scene_id,
                required=target_duration,
                available=round(target_duration - remaining, 3),
            )
        return layers

    # ------------------------------------------------------------------
    def _plan_multi_assets(
        self,
        scene_id: str,
        target_duration: float,
        asset_ids: List[str],
        assets: Dict[str, AssetRef],
        pool: Dict[str, float],
        image_motion: bool,
    ) -> List[Layer]:
        """素材富余时的多镜头均分：场景时长均分给多个素材。

        不裁剪（用户创作要求）——每个素材完整播出，通过变速适配镜头时长：
        speed = 素材源时长 / 镜头时长（素材短就加速，长就慢放），
        duration = 镜头时长，source_in=0 播完整段，总时长 = 场景时长对齐。
        """
        layers: List[Layer] = []
        n = len(asset_ids)
        if n == 0:
            return layers
        cursor = 0.0
        for i, aid in enumerate(asset_ids):
            asset = assets[aid]
            seg = (round(target_duration / n, 3) if i < n - 1
                   else round(target_duration - cursor, 3))
            if seg <= 0:
                continue
            if asset.type == "video":
                dur = asset.duration or seg
                speed = round(dur / seg, 3) if seg > 0 else 1.0
                source_out = round(dur, 3)
                if speed > config.SPEED_FAST_FORWARD_MAX:
                    # 6x 仍不够放完整素材 → 裁剪到 6x 内（画面可用性极限）
                    speed = config.SPEED_FAST_FORWARD_MAX
                    source_out = round(seg * speed, 3)
                layers.append(Layer(
                    type=LayerType.video,
                    asset_id=aid,
                    start=round(cursor, 3),
                    duration=round(seg, 3),
                    speed=speed,
                    source_in=0.0,
                    source_out=source_out,
                ))
                pool[aid] = pool.get(aid, 0.0) + source_out
                cursor = round(cursor + seg, 3)
            else:
                if seg < config.IMAGE_MIN_DURATION:
                    continue
                motion = "slow_zoom_in" if (image_motion and seg >= config.IMAGE_MOTION_MIN_DURATION) else None
                layers.append(Layer(
                    type=LayerType.image, asset_id=aid,
                    start=round(cursor, 3), duration=round(seg, 3), motion=motion,
                ))
                cursor = round(cursor + seg, 3)
        return layers


    def _fit_video(self, available: float, target: float, image_fallback: bool = False,
                   forced_speed: Optional[float] = None) -> tuple:
        """返回 (take_source_seconds, speed, output_duration)。

        V2 §12 双层策略：
        - required = available / target
        - required > 1.70            → 1.70x + 裁剪（禁止极端加速）
        - 0.90 <= required <= 1.50  → 直接变速（preferred）
        - 0.80 <= required < 0.90   → 有图片兜底则 0.90x 播完交补时（Case 03/04）；
                                      无图片兜底则允许慢放覆盖（absolute 内合法）
        - 0.80 < required 不可达     → 0.90x 播完，剩余交补时/MISSING
        """
        if forced_speed is not None:
            try:
                speed = float(forced_speed)
            except (TypeError, ValueError):
                speed = 1.0
            speed = max(config.SPEED_ABSOLUTE_MIN, min(config.SPEED_ABSOLUTE_MAX, speed))
            take = min(available, max(0.0, target) * speed)
            return take, round(speed, 3), round(take / speed, 3) if speed > 0 else 0.0

        required = available / target if target > 0 else float("inf")

        if required > config.SPEED_ABSOLUTE_MAX:
            # 极端加速禁止：1.70x + 裁剪
            take = min(available, config.SPEED_ABSOLUTE_MAX * target)
            return take, config.SPEED_ABSOLUTE_MAX, round(take / config.SPEED_ABSOLUTE_MAX, 3)
        if required >= config.SPEED_PREFERRED_MIN:
            # preferred/absolute 区间内：直接变速覆盖
            speed = round(required, 3)
            return available, speed, round(available / speed, 3)
        # required < 0.90（视频相对目标偏短）
        if image_fallback:
            # 有图片兜底：不慢放，0.90x 播完视频，剩余交图片（Case 03/04 期待行为）
            return available, config.SPEED_PREFERRED_MIN, round(
                available / config.SPEED_PREFERRED_MIN, 3
            )
        if required >= config.SPEED_ABSOLUTE_MIN:
            # 无图片兜底：允许慢放到 absolute 边界覆盖目标（不误报 MISSING_ASSET）
            speed = round(required, 3)
            return available, speed, round(available / speed, 3)
        # required < 0.80：慢放也不够，0.90x 播完诚实交 MISSING
        return available, config.SPEED_PREFERRED_MIN, round(
            available / config.SPEED_PREFERRED_MIN, 3
        )


class LLMPlanner:
    """LLM 规划器（Gate 3）：13 条硬约束进 prompt，只输出 JSON。

    llm_call 由上层注入（MPT llm 能力或测试 mock）：
        llm_call(prompt: str) -> str
    """

    PROMPT = """你是 Script-driven Video Timeline Planner。

你的唯一任务：把视觉素材分配到 Narration Timeline。

Narration Timeline 是绝对主时间轴。

你必须：
1. 覆盖整个 narration duration
2. 不产生 gap
3. 不产生 overlap
4. 不改变用户原文
5. 优先使用与 Scene 对应素材
6. 视频允许变速（speed 在 [{abs_min}, {abs_max}]，优先 [{pref_min}, {pref_max}]）
7. 视频允许裁剪（source_in/source_out）
8. 视频不足时允许图片补时
9. 图片可以单独构成整个 Scene
10. 禁止创造不存在的素材
11. 禁止输出不存在的文件
12. 禁止输出 FFmpeg 命令
13. 只能输出 JSON

每层 layer 的 duration 必须满足：duration = (source_out - source_in) / speed（视频）。
同一场景内 layers 按 start 排序后必须无缝铺满场景时长。
同一视频素材的 (source_in, source_out) 区间不得重叠。
转场 transition_out.type 只能是：{transitions}。
图片 motion 只能是：{motions} 或 null。

素材清单（asset_id: type, duration_s, prompt）：
{assets}

旁白场景（id: start, duration, text）：
{scenes}

输出 JSON：
{{
  "scenes": [
    {{
      "id": "S01",
      "layers": [
        {{"type": "video", "asset_id": "...", "start": 0.0, "duration": 4.82,
          "speed": 1.45, "source_in": 0.0, "source_out": 7.0}}
      ],
      "transition_out": {{"type": "cut"}}
    }}
  ]
}}

素材不足时输出：
{{"status": "MISSING_ASSET", "scene_id": "S05", "required_duration": 13, "available_duration": 8}}

只输出 JSON。"""

    def __init__(self, llm_call: PlannerLLM):
        self._llm_call = llm_call

    def plan(
        self,
        scenes: List[dict],
        assets: Dict[str, AssetRef],
        asset_order: List[str],
        width: int = config.DEFAULT_RESOLUTION[0],
        height: int = config.DEFAULT_RESOLUTION[1],
        fps: int = config.DEFAULT_FPS,
        **_unused,
    ) -> CompositionIR:
        assets_desc = "\n".join(
            f"- {aid}: {assets[aid].type}, duration_s={assets[aid].duration}"
            + (f", prompt={assets[aid].prompt}" if assets[aid].prompt else "")
            for aid in asset_order
        )
        scenes_desc = "\n".join(
            f"- {s['id']}: start={s['start']:.2f}, duration={s['duration']:.2f}, text={s['text']}"
            for s in scenes
        )
        prompt = self.PROMPT.format(
            abs_min=config.SPEED_ABSOLUTE_MIN,
            abs_max=config.SPEED_ABSOLUTE_MAX,
            pref_min=config.SPEED_PREFERRED_MIN,
            pref_max=config.SPEED_PREFERRED_MAX,
            transitions=", ".join(registry.TRANSITION_REGISTRY),
            motions=", ".join(registry.MOTION_REGISTRY),
            assets=assets_desc,
            scenes=scenes_desc,
        )
        raw = self._llm_call(prompt)
        parsed = json.loads(extract_json(raw))

        if parsed.get("status") == "MISSING_ASSET":
            raise MissingAssetError(
                scene_id=str(parsed.get("scene_id", "?")),
                required=float(parsed.get("required_duration", 0)),
                available=float(parsed.get("available_duration", 0)),
            )

        scene_map = {s["id"]: s for s in scenes}
        scene_irs: List[SceneIR] = []
        for planned in parsed.get("scenes", []):
            src = scene_map.get(planned["id"])
            if src is None:
                raise ValueError(f"LLM 输出了不存在的场景: {planned['id']}")
            layers = []
            for ltext in planned.get("layers", []):
                asset = assets.get(ltext.get("asset_id", ""))
                if asset is None:
                    raise MissingAssetError(
                        scene_id=planned["id"], required=0.0, available=0.0,
                        message=f"LLM 引用不存在的素材 {ltext.get('asset_id')}",
                    )
                ltype = LayerType(ltext["type"])
                layers.append(Layer(
                    type=ltype,
                    asset_id=asset.id,
                    start=float(ltext.get("start", 0.0)),
                    duration=float(ltext["duration"]),
                    speed=float(ltext.get("speed", 1.0)),
                    source_in=float(ltext.get("source_in", 0.0)),
                    source_out=(
                        float(ltext["source_out"])
                        if ltext.get("source_out") is not None else None
                    ),
                    motion=ltext.get("motion"),
                ))
            transition = TransitionOut(
                type=planned.get("transition_out", {}).get("type", config.TRANSITION_DEFAULT)
            )
            scene_irs.append(SceneIR(
                id=src["id"],
                start=float(src["start"]),
                duration=float(src["duration"]),
                text=src["text"],
                layers=layers,
                transition_out=transition,
            ))

        total = scenes[-1]["start"] + scenes[-1]["duration"]
        return CompositionIR(
            project=ProjectSpec(width=width, height=height, fps=fps, duration=round(total, 3)),
            scenes=scene_irs,
            audio=[],
        )


def extract_json(raw: str) -> str:
    """剥掉 LLM 输出外层的 markdown 代码块，返回 JSON 文本。"""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"LLM 输出无 JSON: {raw[:200]}")
    return text[start : end + 1]
