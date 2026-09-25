"""商用 Preflight（IG-PREFLIGHT）：发布/渲染前的 License 与完整性闸门。

全局契约 §42：Publish 前必须检查 Asset/License/Provenance/Composition
validity/Missing media/Invalid references。任何 BLOCKED/UNKNOWN 不进商业发布。

四态规则（方案 §二十一/§四十）：
    用户上传 (origin=user)      → CLEAR（授权用户负责，系统记来源）
    内置自有 (origin=own)       → 按 provenance 台账（bgm/sfx 自有= CLEAR；
                                 edge-tts 配音 = REVIEW，正式商用前须换）
    AI 生成 (origin=generated)  → CLEAR（lineage 记录生成参数）
    无 provenance 记录          → UNKNOWN → REJECT（不能"看起来完整"就放行）
    明确 BLOCKED 台账          → REJECT（如 shotcraft 仓库第三方素材）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

from video_director import config
from video_director.models import CompositionIR


@dataclass
class PreflightIssue:
    code: str
    target_id: str
    message: str
    recoverable: bool = True


@dataclass
class PreflightReport:
    status: str  # PASS | REJECT
    issues: List[PreflightIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "issues": [
                {"code": i.code, "targetId": i.target_id,
                 "message": i.message, "recoverable": i.recoverable}
                for i in self.issues
            ],
        }


# ---------------------------------------------------------------------------
# 资产 → provenance 映射（资产 id 前缀路由，preflight 的核心规则表）
# ---------------------------------------------------------------------------

def _asset_origin(asset_id: str, manifest_entry: Optional[dict]) -> str:
    """推断资产来源 origin（user/own/generated/third-party/unknown）。

    诚实规则：manifest 条目缺 source 字段 → unknown（UNKNOWN → REJECT），
    不能默认当 user 放行——来源不明就是不明（契约 §16）。
    """
    if manifest_entry:
        src = (manifest_entry.get("source") or "").strip()
        if src in ("user", "own", "generated", "third-party"):
            return src
        if not src:
            return "unknown"  # 条目存在但未记录来源 → UNKNOWN
    # 前缀路由：内置音频库
    if asset_id.startswith(("bgm-", "sfx-")):
        if "upload" in asset_id:
            return "user"       # 本地上传的音频（/api/audio_upload 注册）
        return "own"            # 内置库（自有合成/录制）
    return "unknown"


def _commercial_status_of(origin: str, provenance_records: Dict[str, object]) -> Optional[str]:
    """origin → 四态。unknown origin 无 provenance → UNKNOWN（REJECT 依据）。"""
    if origin == "user":
        return "CLEAR"
    if origin == "own":
        # 内置资产按台账；配音（edge-tts）REVIEW
        return None  # 由调用方查台账细分
    if origin == "generated":
        return "CLEAR"
    return "UNKNOWN"


def run_preflight(project_id: str, for_publish: bool = True) -> PreflightReport:
    """项目商用预检。

    检查项（契约 §42）：
    1. 素材存在（manifest 引用的文件在盘）
    2. 每个引用资产的 provenance 四态（UNKNOWN/BLOCKED → REJECT）
    3. Composition 有效性（composition.json 可解析）
    4. 配音来源（edge-tts 公共端点 = REVIEW，发布前提示）
    5. BGM/SFX 来源（内置自有 CLEAR；上传 user CLEAR；无记录 UNKNOWN）

    for_publish=True：REVIEW 也计入 issue（要求处理）；
    for_publish=False（草稿/预览）：REVIEW 仅警告不阻断。
    """
    issues: List[PreflightIssue] = []
    base = config.project_dir(project_id)

    # 1. Composition 有效性
    comp_path = base / "plans" / "composition.json"
    ir: Optional[CompositionIR] = None
    if not comp_path.is_file():
        return PreflightReport("REJECT", [PreflightIssue(
            "COMPOSITION_MISSING", project_id, "项目无 composition.json（先完成规划）", False,
        )])
    try:
        ir = CompositionIR(**json.loads(comp_path.read_text(encoding="utf-8")))
    except Exception as exc:
        return PreflightReport("REJECT", [PreflightIssue(
            "COMPOSITION_INVALID", project_id, f"composition.json 解析失败: {exc}", False,
        )])

    # 2. manifest 素材存在性 + provenance
    manifest_path = base / "assets" / "manifest.json"
    manifest: List[dict] = []
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            issues.append(PreflightIssue(
                "MANIFEST_INVALID", project_id, "manifest.json 损坏", False))

    # 引用的素材集合（IR 层 + 音频轨）
    referenced: set = set()
    for sc in ir.scenes:
        for lay in sc.layers:
            if lay.asset_id:
                referenced.add(lay.asset_id)
    for tr in ir.audio:
        if tr.asset_id:
            referenced.add(tr.asset_id)

    # 叙述者（narration）来源判断：项目 params 里的 voice 用 edge-tts → REVIEW
    params_path = base / "plans"
    # narration 音轨本身无 asset_id（合成产物），检查项目参数记录的 voice
    # （compose 时 params_json 里 voice 字段；此处从 run.json/storyboard 读）
    storyboard_p = base / "plans" / "storyboard.json"
    voice_hint = None
    if storyboard_p.is_file():
        try:
            sb = json.loads(storyboard_p.read_text(encoding="utf-8"))
            voice_hint = sb.get("voice")
        except Exception:
            pass

    manifest_by_id = {a["id"]: a for a in manifest if isinstance(a, dict) and "id" in a}
    # 音频库资产（bgm-/sfx- 前缀）不在项目 manifest，查全局音频库注册表
    audio_lib_by_id = {}
    audio_lib_p = config.composer_storage_root() / "audio_library" / "audio_manifest.json"
    if audio_lib_p.is_file():
        try:
            for a in json.loads(audio_lib_p.read_text(encoding="utf-8")):
                audio_lib_by_id[a.get("id", "")] = a
        except Exception:
            pass

    for asset_id in sorted(referenced):
        entry = manifest_by_id.get(asset_id) or audio_lib_by_id.get(asset_id)
        origin = _asset_origin(asset_id, entry)
        # 明确 BLOCKED 台账检查（shotcraft 第三方素材等）
        if entry and (entry.get("provenance_id") == "src-shotcraft-assets"
                      or entry.get("source") == "shotcraft-assets"):
            issues.append(PreflightIssue(
                "ASSET_LICENSE_BLOCKED", asset_id,
                "素材来自 Video Shotcraft 仓库第三方资产（BLOCKED 台账），禁止商业发布", False))
            continue
        if origin == "unknown":
            issues.append(PreflightIssue(
                "ASSET_LICENSE_UNKNOWN", asset_id,
                "素材无 provenance 记录（UNKNOWN），不能用于商业发布", False))
            continue
        if origin == "own":
            # 内置库资产按 provenance 台账细分
            from video_director.registries.provenance import load_provenance_all
            prov = load_provenance_all()
            rec = prov.get("sfx-builtin") if asset_id.startswith("sfx-") else prov.get("bgm-builtin")
            status = rec.commercial_status if rec else "UNKNOWN"
            if status == "UNKNOWN":
                issues.append(PreflightIssue(
                    "ASSET_LICENSE_UNKNOWN", asset_id, "内置素材无台账记录", False))
            elif status == "REVIEW" and for_publish:
                issues.append(PreflightIssue(
                    "ASSET_LICENSE_REVIEW", asset_id,
                    f"素材 {asset_id} 授权需人工核查（REVIEW）：{rec.notes if rec else ''}", True))

    # 3. 素材文件存在（manifest 引用的文件在盘）
    for asset_id in sorted(referenced):
        entry = manifest_by_id.get(asset_id)
        if entry and entry.get("file") and not Path(entry["file"]).is_file():
            issues.append(PreflightIssue(
                "ASSET_FILE_MISSING", asset_id,
                f"素材文件缺失: {entry['file']}", False))

    # 4. 配音 REVIEW（edge-tts 公共端点；正式商用前须替换）——只在发布模式提示
    if for_publish and voice_hint and ir.audio:
        has_narration = any(tr.type.value == "narration" for tr in ir.audio)
        if has_narration:
            issues.append(PreflightIssue(
                "TTS_LICENSE_REVIEW", "narration",
                "配音来自 edge-tts 公共端点（REVIEW）：正式商用发布前请替换为已授权 TTS 服务", True))

    status = "REJECT" if any(not i.recoverable for i in issues) else "PASS"
    report = PreflightReport(status, issues)
    logger.info(
        f"preflight {project_id}: {status} ({len(issues)} issues, "
        f"publish_mode={for_publish})"
    )
    return report
