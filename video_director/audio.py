"""Audio Library 与 Resolver（V2 §35-§38）。

独立于 MPT resource/songs（其默认音乐有 YouTube 来源版权风险，V2 §36）。
第一版：本地库目录扫描 + license 台账；Resolver 按 semantic intent 选 asset_id。
无合法素材时诚实 NO_BGM（V2 §93），绝不随便抓一首。

测试用 ffmpeg 合成自产音频（license=self-generated）保证闭环可验证。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

from video_director import prober
from video_director.models import AssetRef, AudioTrack, AudioTrackType


class AudioLibraryError(Exception):
    pass


#: 音频库根目录（storage/composer/audio_library/，与项目产物平行）
def library_root() -> Path:
    root = Path(__file__).resolve().parent.parent / "storage" / "composer" / "audio_library"
    root.mkdir(parents=True, exist_ok=True)
    return root


def scan_library() -> Dict[str, AssetRef]:
    """扫描本地音频库：每个文件必须有同目录 audio_manifest.json 条目。

    manifest 结构（V2 §35）：
        [{"asset_id": "...", "file": "bgm/xxx.mp3", "type": "bgm|sfx|ambient",
          "tags": [...], "license": {"source": "...", "commercial_use": true}}]
    """
    root = library_root()
    audio_assets: Dict[str, AssetRef] = {}
    manifests = list(root.rglob("audio_manifest.json"))
    if not manifests:
        return audio_assets
    for mf in manifests:
        try:
            with open(mf, "r", encoding="utf-8") as f:
                entries = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(f"audio manifest 解析失败 {mf}: {exc}")
            continue
        for e in entries:
            file_path = mf.parent / e["file"]
            if not file_path.is_file():
                logger.warning(f"音频库条目文件缺失: {file_path}")
                continue
            license_info = e.get("license", {})
            audio_assets[e["asset_id"]] = AssetRef(
                id=e["asset_id"],
                type=e.get("type", "bgm"),
                file=str(file_path.resolve()),
                duration=None,  # 惰性探测
                keywords=e.get("tags", []),
                source=license_info.get("source", "unknown"),
                license_note=json.dumps(license_info, ensure_ascii=False),
            )
    return audio_assets


def resolve(
    intent: dict,
    library: Dict[str, AssetRef],
) -> Optional[str]:
    """Audio Asset Resolver（V2 §37）：AI 只给 semantic intent，返回 asset_id。

    找不到返回 None（上层输出 NO_BGM / NO_SFX，诚实降级）。
    intent: {"type": "bgm|sfx", "semantic_intent": "soft whoosh", "tags": [...]}
    """
    want_type = intent.get("type", "bgm")
    want_tags = set(intent.get("tags", []))
    want_text = intent.get("semantic_intent", "").lower()

    candidates = [a for a in library.values() if a.type == want_type]
    if not candidates:
        return None
    # tag 匹配优先
    scored = []
    for a in candidates:
        tags = set(a.keywords)
        overlap = len(tags & want_tags) if want_tags else 0
        text_hit = 1 if want_text and any(want_text in k.lower() for k in a.keywords) else 0
        scored.append((overlap + text_hit, a.id))
    scored.sort(key=lambda x: -x[0])
    best_score, best_id = scored[0]
    if best_score <= 0 and want_tags:
        return None  # 明确指定了 tag 却没有匹配，诚实返回无
    return best_id


def build_bgm_track(
    asset_id: str,
    project_duration: float,
    volume: float,
) -> AudioTrack:
    return AudioTrack(
        type=AudioTrackType.bgm,
        asset_id=asset_id,
        start=0.0,
        duration=project_duration,
        volume=volume,
        ducking=True,
    )


def build_sfx_track(
    asset_id: str,
    start: float,
    duration: float,
    volume: float = None,
) -> AudioTrack:
    from video_director import config as cfg
    return AudioTrack(
        type=AudioTrackType.sfx,
        asset_id=asset_id,
        start=start,
        duration=duration,
        volume=volume if volume is not None else cfg.SFX_DEFAULT_VOLUME,
    )


# ---------------------------------------------------------------------------
# SFX 动作绑定（guizang audio-cues 思想：音效绑定动作发生点，非"每镜头开头
# 放一个 whoosh"。AI 只声明 action_id + role，Resolver 按 action 语义选 SFX 类别。
# ---------------------------------------------------------------------------

#: action_id → SFX 语义类别映射（guizang：click/whoosh/impact/resolve 家族）
ACTION_SFX_MAP = {
    # 转场/移动类 → whoosh 家族
    "transition": ["whoosh"],
    "cut": ["whoosh"],
    "broll_in": ["whoosh"],
    "slide": ["whoosh"],
    # 强调/冲击类 → impact 家族
    "emphasis": ["impact"],
    "reveal": ["impact"],
    "punch": ["impact"],
    "turning_point": ["impact"],
    # 交互反馈类 → click 家族
    "text_appear": ["click"],
    "toggle": ["click"],
    "select": ["click"],
    "feedback": ["click"],
    # 结果/通知类 → 优先 click（当前库无 ding，类别回退）
    "result": ["click"],
    "notification": ["click"],
    "success": ["click"],
}


def resolve_action_sfx(event: dict, library: Dict[str, AssetRef]) -> Optional[str]:
    """动作事件 → SFX asset_id（guizang 按类别回退：先精确 tag 再类别，缺则 None）。

    event: {action_id, at, role, tags?} —— action_id 驱动语义，role 可选
    （feedback/emphasis/transition），tags 显式指定时优先。
    """
    action_id = event.get("action_id", "")
    explicit_tags = event.get("tags") or []
    if explicit_tags:
        return resolve({"type": "sfx", "tags": explicit_tags}, library)

    # action_id 语义类别（含 role fallback：emphasis 类 role 同 impact）
    categories = ACTION_SFX_MAP.get(action_id, None)
    if categories is None:
        role = event.get("role", "")
        if role in ("emphasis", "turning_point"):
            categories = ["impact"]
        elif role == "transition":
            categories = ["whoosh"]
        elif role == "feedback":
            categories = ["click"]
    if not categories:
        return None
    return resolve({"type": "sfx", "tags": categories}, library)


def normalize_sfx_events(events: List[dict]) -> List[dict]:
    """统一 SFX 事件输入为 {at, action_id?, tags?, asset_id?, role?}（向后兼容）。

    旧格式 {start, tags/asset_id} 与新格式 {at, action_id} 都归一为
    {at: float}，供 pipeline 消费。
    """
    normalized = []
    for ev in events:
        at = ev.get("at", ev.get("start", 0.0))
        item = {
            "at": float(at),
            "action_id": ev.get("action_id"),
            "tags": ev.get("tags"),
            "asset_id": ev.get("asset_id"),
            "role": ev.get("role"),
            "duration": ev.get("duration", 0.7),
            "volume": ev.get("volume"),
        }
        normalized.append(item)
    return normalized


def append_audio_assets(
    base_assets: Dict[str, AssetRef],
    audio_assets: Dict[str, AssetRef],
) -> Dict[str, AssetRef]:
    """把音频库资产并入渲染用资产表（渲染器按 asset_id 找文件）。"""
    merged = dict(base_assets)
    for aid, a in audio_assets.items():
        merged[aid] = a
    return merged


def ensure_audio_duration(asset: AssetRef) -> AssetRef:
    """惰性探测音频时长（缺失时补）。"""
    if asset.duration is None:
        probe = prober.probe_media(asset.file)
        asset.duration = probe.duration
    return asset


# ---------------------------------------------------------------------------
# 测试音频生成（自产素材，license=self-generated）
# ---------------------------------------------------------------------------

def generate_test_audio_library(force: bool = False) -> Path:
    """生成自产测试音频库：1 BGM + 3 SFX（ffmpeg 合成，无版权风险）。

    BGM: 30s 轻柔正弦和弦垫底（E2/G3/B3 混音）
    SFX: whoosh（频率扫描）/ impact（低频冲击）/ click（短促脉冲）
    """
    import subprocess

    root = library_root()
    bgm_dir = root / "bgm"
    sfx_dir = root / "sfx"
    bgm_dir.mkdir(parents=True, exist_ok=True)
    sfx_dir.mkdir(parents=True, exist_ok=True)
    ff = prober._resolve_ffmpeg()

    bgm_path = bgm_dir / "ambient-pad-001.mp3"
    sfx_whoosh = sfx_dir / "whoosh-001.mp3"
    sfx_impact = sfx_dir / "impact-001.mp3"
    sfx_click = sfx_dir / "click-001.mp3"

    def run_ffmpeg(args: List[str]):
        completed = subprocess.run(
            [ff, "-y", "-loglevel", "error"] + args,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        if completed.returncode != 0:
            raise AudioLibraryError(f"测试音频生成失败: {completed.stderr[-300:]}")

    if force or not bgm_path.is_file():
        # 和弦垫底：三个正弦波 A2/C#3/E4（110/138.6/330Hz），慢包络
        run_ffmpeg([
            "-f", "lavfi", "-i",
            "sine=frequency=110:duration=30,volume=0.25",
            "-f", "lavfi", "-i",
            "sine=frequency=138.6:duration=30,volume=0.18",
            "-f", "lavfi", "-i",
            "sine=frequency=330:duration=30,volume=0.08",
            "-filter_complex",
            "[0:a][1:a][2:a]amix=inputs=3:duration=longest,"
            "afade=t=in:st=0:d=2,afade=t=out:st=28:d=2,"
            "aresample=44100,aformat=channel_layouts=mono",
            "-c:a", "libmp3lame", "-b:a", "128k",
            str(bgm_path),
        ])
    if force or not sfx_whoosh.is_file():
        # whoosh：白噪声 + 带通频率扫描（aevalsrc 近似）
        run_ffmpeg([
            "-f", "lavfi", "-i", "anoisesrc=color=white:duration=0.7:amplitude=0.4",
            "-af", "lowpass=f=4000,highpass=f=300,"
                   "afade=t=in:st=0:d=0.25,afade=t=out:st=0.4:d=0.3",
            "-c:a", "libmp3lame", str(sfx_whoosh),
        ])
    if force or not sfx_impact.is_file():
        run_ffmpeg([
            "-f", "lavfi", "-i", "sine=frequency=60:duration=0.5",
            "-af", "afade=t=out:st=0.05:d=0.45,volume=1.8,"
                   "aresample=44100,aformat=channel_layouts=mono",
            "-c:a", "libmp3lame", str(sfx_impact),
        ])
    if force or not sfx_click.is_file():
        run_ffmpeg([
            "-f", "lavfi", "-i", "sine=frequency=1200:duration=0.06",
            "-af", "afade=t=out:st=0.01:d=0.05,volume=0.9",
            "-c:a", "libmp3lame", str(sfx_click),
        ])

    # manifest（license 台账，V2 §38）
    manifest_path = root / "audio_manifest.json"
    manifest = [
        {
            "asset_id": "bgm-ambient-001", "file": "bgm/ambient-pad-001.mp3", "type": "bgm",
            "tags": ["calm", "ambient", "documentary", "pad"],
            "license": {"source": "self-generated", "commercial_use": True,
                        "attribution_required": False},
        },
        {
            "asset_id": "sfx-whoosh-001", "file": "sfx/whoosh-001.mp3", "type": "sfx",
            "tags": ["transition", "whoosh", "soft"],
            "license": {"source": "self-generated", "commercial_use": True,
                        "attribution_required": False},
        },
        {
            "asset_id": "sfx-impact-001", "file": "sfx/impact-001.mp3", "type": "sfx",
            "tags": ["emphasis", "impact", "hit"],
            "license": {"source": "self-generated", "commercial_use": True,
                        "attribution_required": False},
        },
        {
            "asset_id": "sfx-click-001", "file": "sfx/click-001.mp3", "type": "sfx",
            "tags": ["text", "click", "ui"],
            "license": {"source": "self-generated", "commercial_use": True,
                        "attribution_required": False},
        },
    ]
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    logger.info(f"test audio library ready: {manifest_path}")
    return manifest_path
