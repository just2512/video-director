"""Timeline-native Generation（EDT-008 / §十六 / §31 / §五十一 Capability G）。

选时间范围 → Generate → Provider → 结果变体 → 加入时间线 → 素材落地。

能力边界（诚实，按 provider 配置动态分流）：
    image   = AI 生图（roles["image"]，OpenAI Images 协议 images.generate；
              未配置 image key → 兜底 Graphics Runtime 程序化视觉卡，仍可用）
    graphic = 程序化视觉卡（自有 Graphics Runtime，确定性 PNG，无外部依赖）
    video   = AI 生视频（roles["video"]，OpenAI 兼容 video/generations 异步任务；
              未配置 video key（或供应商协议不符）→ 诚实报错，禁 fake success）
    sfx     = 无合成音 provider → GENERATION_UNAVAILABLE（禁 fake）

结果变体 3 个（§31「结果 A/B/C」）。

Lineage（§23/§31）：每个结果写 generation_request_id / provider id /
input_fingerprint（prompt 哈希）/ output_fingerprint（文件哈希）进 manifest。
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from loguru import logger

from video_director import config


class GenerationError(Exception):
    """生成失败（诚实暴露，禁 fake success）。

    终审分类（配额 vs 临时故障）：供应商余额耗尽（429/insufficient quota/
    欠费/余额不足）与网络临时故障（超时/连接失败）须区别对待——前者
    recoverable=False，充值或换供应商才有解，重试只会重复付费。
    """

    #: 配额类关键词（供应商余额耗尽；命中即 QUOTA_EXCEEDED，不可自恢复）
    QUOTA_MARKERS = ("429", "insufficient", "quota", "余额", "欠费", "billing")
    #: 临时故障关键词（超时/连接失败；可重试）
    TRANSIENT_MARKERS = ("timeout", "timed out", "connection", "connectionerror", "econnreset")

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        low = f"{message}".lower()
        self.is_quota_err = any(m in low for m in self.QUOTA_MARKERS)
        self.is_transient = any(m in low for m in self.TRANSIENT_MARKERS)
        if self.is_quota_err:
            # 配额耗尽不可自恢复：code 归一为 QUOTA_EXCEEDED，message 原样保留
            # （供应商原文（含「余额不足」等中文提示）用户要能看懂）
            self.code = "QUOTA_EXCEEDED"
            self.recoverable = False
        else:
            self.recoverable = self.is_transient
        super().__init__(f"[{self.code}] {message}")

    def is_quota(self) -> bool:
        """供应商余额/配额耗尽（重试无解，需充值或换供应商）。"""
        return self.is_quota_err

    def is_recoverable(self) -> bool:
        """临时故障（网络超时等），重试可能恢复。"""
        return self.recoverable


#: 本系统真实可用的生成类型（sfx 无 provider，诚实不可用）
SUPPORTED_TYPES = ("image", "graphic", "video", "motion")

#: 3 个确定性变体名（§31 结果 A/B/C）
VARIANTS = ("A", "B", "C")


def _fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


# ---------------------------------------------------------------------------
# AI 生图 / 生视频 provider（§五十一 Capability G：Image/Video 正式接线）
# ---------------------------------------------------------------------------

def _role_cfg(role: str) -> dict:
    from video_director import model_config as vd_mc
    return vd_mc.get_role_config(role)


def _image_size(width: int, height: int) -> str:
    """画幅 → OpenAI Images 协议 size（默认 1024 级，按比例映射）。"""
    if abs(width - height) <= max(width, height) * 0.02:
        return "1024x1024"
    if height > width:
        return "1024x1792"  # 竖屏 9:16
    return "1792x1024"      # 横屏 16:9


# ---------------------------------------------------------------------------
# 视频异步任务持久化（仓库审计差距 2 残余：纯内存轮询 → 进程中断后外部任务
# 失联 → 重复付费。落盘 generation_jobs.json，提交即记 provider_task_id，
# 轮询更新，recover_pending_video_jobs 接管恢复）
# ---------------------------------------------------------------------------

def _jobs_path(project_id: str) -> Path:
    return config.project_dir(project_id) / "generation_jobs.json"


def _load_jobs(project_id: str) -> dict:
    """Load durable provider jobs without treating corruption as an empty store.

    An unreadable ledger can contain a provider task that was already charged.
    Returning {} here would permit a duplicate paid submission, so only a truly
    absent file represents an empty project ledger.
    """
    p = _jobs_path(project_id)
    try:
        metadata = p.lstat()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise GenerationError(
            "VIDEO_JOB_STORE_UNREADABLE",
            f"视频任务台账状态无法读取；为避免重复提交，已停止生成：{exc}",
        ) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise GenerationError(
            "VIDEO_JOB_STORE_UNSAFE",
            f"视频任务台账不是安全的普通文件，已拒绝读取：{p}",
        )
    try:
        raw = p.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise GenerationError(
            "VIDEO_JOB_STORE_UNREADABLE",
            f"视频任务台账无法读取；为避免重复提交，已停止生成：{exc}",
        ) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GenerationError(
            "VIDEO_JOB_STORE_CORRUPT",
            f"视频任务台账 JSON 损坏；为避免重复提交，已停止生成：{exc}",
        ) from exc
    if not isinstance(data, dict) or any(
        not isinstance(job_id, str) or not isinstance(job, dict)
        for job_id, job in data.items()
    ):
        raise GenerationError(
            "VIDEO_JOB_STORE_INVALID",
            "视频任务台账结构无效；为避免重复提交，已停止生成",
        )
    return data


def _save_jobs(project_id: str, jobs: dict) -> None:
    """Atomically persist the provider ledger without following a predictable temp path."""
    path = _jobs_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(jobs, ensure_ascii=False, indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _jobs_lock(project_id: str):
    """generation_jobs.json 跨进程/跨线程写锁（filelock 已装，勿手写）。

    并发场景真实存在：webui 线程池的 _update_job + recover_pending_video_jobs
    后台恢复同写一个 project——原来的读-改-写无锁，最后一写覆盖中间更新。
    ponytail: 全局单锁；吞吐不足时再拆 per-job 锁。
    FileLock 的 lock 文件会在释放后保留为稳定 inode。不要 unlink：
    其他进程可能已经等待旧 inode，删除后新进程创建同名文件会造成双锁分裂。
    """
    from filelock import FileLock
    lock_path = _jobs_path(project_id).with_suffix(".lock")
    return FileLock(str(lock_path), timeout=30)

def _video_submission_lock(project_id: str, input_fp: str, variant: str):
    """Serialize provider submission for one project/fingerprint/variant.

    The lookup -> provider POST -> provider_task_id persistence sequence must be
    atomic with respect to competing callers. Locking only _update_job is too
    late: two callers can both observe no reusable job and both incur a charge.
    Keep this lock separate from the jobs read/modify/write lock, and never
    unlink its stable lock file.
    """
    from filelock import FileLock
    key = hashlib.sha256(f"{input_fp}|{variant}".encode("utf-8")).hexdigest()
    lock_path = _jobs_path(project_id).with_name(f".generation-submit-{key}.lock")
    return FileLock(str(lock_path), timeout=120)


def _update_job(project_id: str, job_id: str, fields: dict) -> dict:
    """增量更新一条 job 记录并落盘（全程持锁：webui 线程池 + 后台恢复并发安全）。"""
    with _jobs_lock(project_id):
        jobs = _load_jobs(project_id)
        job = jobs.get(job_id) or {}
        job.update(fields)
        job["job_id"] = job_id
        job["updated_at"] = fields.get("updated_at", time.time())
        jobs[job_id] = job
        _save_jobs(project_id, jobs)
    return job


def _prompt_fingerprint(prompt: str, gen_type: str) -> str:
    return hashlib.sha256((prompt + "|" + gen_type).encode("utf-8")).hexdigest()


def _find_reusable_job(project_id: str, input_fp: str, variant: str) -> Optional[dict]:
    """同 fingerprint+variant 且仍在跑的外部任务 → 复用轮询，不重复提交付费。"""
    for job in _load_jobs(project_id).values():
        reusable_status = job.get("status") in ("pending", "running", "downloading")
        recovered_output = (
            job.get("status") == "done"
            and (
                job.get("recovery_needs_registration") is True
                or job.get("manifest_registered") is True
            )
        )
        if (job.get("input_fingerprint") == input_fp
                and job.get("variant") == variant
                and (reusable_status or recovered_output)
                and job.get("provider_task_id")):
            return job
    return None


def _find_unresolved_submission(project_id: str, input_fp: str, variant: str) -> Optional[dict]:
    """Find a durable reservation whose provider submission outcome is unknown."""
    for job in _load_jobs(project_id).values():
        if (job.get("input_fingerprint") == input_fp
                and job.get("variant") == variant
                and job.get("status") in ("submitting", "submission_unknown")
                and not job.get("provider_task_id")):
            return job
    return None


def _video_client(cfg: dict):
    """OpenAI 兼容 client 构造（集中一处，测试可 monkeypatch 隔离）。"""
    try:
        from openai import OpenAI
        # Video task creation is non-idempotent. SDK-level retries after an
        # ambiguous timeout can submit and charge for the same generation twice.
        return OpenAI(
            api_key=cfg["api_key"],
            base_url=cfg.get("base_url") or "",
            max_retries=0,
        )
    except Exception as exc:
        raise GenerationError("VIDEO_PROVIDER_UNAVAILABLE", f"openai 库不可用: {exc}") from exc


def _video_output_lock(project_id: str, job_id: str):
    """Serialize finalization of one provider task across poll/recovery workers."""
    from filelock import FileLock
    lock_path = _jobs_path(project_id).with_name(f".generation-output-{job_id}.lock")
    return FileLock(str(lock_path), timeout=120)


def _persist_video_output(project_id: str, job_id: str, out_file: str, url: str) -> Path:
    """Atomically materialize an MP4 before making its job terminal (done)."""
    target = Path(out_file)
    with _video_output_lock(project_id, job_id):
        current = _load_jobs(project_id).get(job_id, {})
        if (current.get("output_commit_protocol") == "atomic-v1"
                and target.is_file() and target.stat().st_size > 0):
            # Covers a crash after atomic replace but before the terminal status write.
            _update_job(project_id, job_id, {
                "status": "done", "output_file": str(target),
                "recovery_needs_registration": True,
            })
            return target
        _update_job(project_id, job_id, {
            "status": "downloading",
            "output_file": str(target),
            "output_commit_protocol": "atomic-v1",
        })
        target.parent.mkdir(parents=True, exist_ok=True)
        # Never write through a predictable "<target>.part" path: a stale or
        # attacker-planted symlink there could redirect provider bytes outside
        # the project. mkstemp creates an exclusive file beside the target so
        # os.replace remains atomic on the same filesystem.
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".part", dir=str(target.parent)
        )
        temporary = Path(temporary_name)
        try:
            payload = _download_bytes(url)
            if not payload:
                raise GenerationError("VIDEO_OUTPUT_EMPTY", "供应商返回的视频文件为空")
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(temporary), str(target))
            _update_job(project_id, job_id, {
                "status": "done", "output_file": str(target),
                "recovery_needs_registration": True,
            })
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise
    return target


def recover_pending_video_jobs(project_id: str) -> List[dict]:
    """进程中断后的视频任务恢复（审计差距 2）。

    读 generation_jobs.json，对 status=running 且有 provider_task_id 的任务
    重新查询外部状态：完成 → 继续下载到 output_file 并标 done；失败 → 标
    failed；查询/下载失败 → 保持 running（下次再试）。幂等：done/failed 不动。
    返回本次发生状态迁移的 job 列表（含最新状态）。
    """
    jobs = _load_jobs(project_id)
    pending = [j for j in jobs.values()
               if j.get("status") in ("running", "downloading")
               and j.get("provider_task_id") and j.get("gen_type") == "video"]
    if not pending:
        return []
    try:
        client = _video_client(_role_cfg("video"))
    except Exception as exc:
        logger.warning(f"generation: 视频任务恢复跳过（provider 不可用，保持 running）: {exc}")
        return []
    recovered: List[dict] = []
    for job in pending:
        job_id = job["job_id"]
        try:
            status, url, _raw = _query_video_status(client, job["provider_task_id"])
        except Exception as exc:
            logger.warning(
                f"generation: 任务 {job_id} 外部查询失败（保持 running）: {exc}")
            continue
        if status == "failed":
            _update_job(project_id, job_id, {"status": "failed"})
            recovered.append({**job, "status": "failed"})
            continue
        if status != "done":
            _update_job(project_id, job_id, {"status": "running"})
            continue
        out_file = job.get("output_file") or ""
        try:
            if out_file and url:
                _persist_video_output(project_id, job_id, out_file, url)
            else:
                _update_job(project_id, job_id, {"status": "done", "output_file": out_file})
        except Exception as exc:
            logger.warning(
                f"generation: 任务 {job_id} 产物下载/落盘失败（保持 downloading，下次恢复）: {exc}")
            continue
        recovered.append({**job, "status": "done", "output_file": out_file})
    return recovered


def generate_image_variants(
    prompt: str,
    width: int,
    height: int,
    count: int = 3,
    variant: Optional[str] = None,
    model_override: Optional[str] = None,
) -> List[dict]:
    """AI 生图（§五十一 Capability G Image）。返回 [{variant, data(bytes), meta}]。

    roles["image"] 有 key → OpenAI Images 协议 images.generate，逐变体呼叫。
    无 key → 抛 GenerationError("IMAGE_PROVIDER_UNAVAILABLE")，由调用方兜底。
    供应商拒绝（size/协议不符）→ 抛 GenerationError("IMAGE_GENERATION_FAILED")，诚实。
    model_override：§38 Model 选择器的显式模型（None 用角色配置）。
    """
    cfg = _role_cfg("image")
    try:
        from video_director import provider_matrix
        provider_matrix.require("image", "image.generate", cfg)
    except provider_matrix.ProviderCapabilityError as exc:
        raise GenerationError(
            "IMAGE_PROVIDER_UNAVAILABLE" if exc.state.status == provider_matrix.MISSING
            else "CAPABILITY_UNSUPPORTED",
            str(exc),
        ) from exc
    if not (cfg or {}).get("api_key"):
        raise GenerationError(
            "IMAGE_PROVIDER_UNAVAILABLE",
            "生图模型未配置（roles.image.api_key 为空）；将走程序化视觉卡兜底",
        )
    model = model_override or cfg.get("model") or "gpt-image-1"
    base_url = cfg.get("base_url") or "https://api.openai.com/v1"
    size = _image_size(width, height)
    try:
        from openai import OpenAI
        client = OpenAI(api_key=cfg["api_key"], base_url=base_url)
    except Exception as exc:
        raise GenerationError("IMAGE_PROVIDER_UNAVAILABLE", f"openai 库不可用: {exc}") from exc

    n = count if variant is None else 1
    out: List[dict] = []
    try:
        resp = client.images.generate(model=model, prompt=prompt, size=size, n=n)
    except Exception as exc:
        # 供应商拒绝/配额/网络故障 → 诚实暴露，不 fake。
        # 配额类（429/insufficient quota/欠费）由 GenerationError.__init__ 自动
        # 归类为 QUOTA_EXCEEDED（message 原样保留，用户能看懂供应商原文）。
        raise GenerationError(
            "IMAGE_GENERATION_FAILED",
            f"生图调用失败（model={model}, size={size}）：{exc}",
        ) from exc
    for i, item in enumerate(resp.data or []):
        raw: bytes = b""
        if getattr(item, "b64_json", None):
            import base64
            raw = base64.b64decode(item.b64_json)
        elif getattr(item, "url", None):
            raw = _download_bytes(item.url)
        if not raw:
            continue
        out.append({
            "variant": (variant or VARIANTS[i % len(VARIANTS)]),
            "data": raw,
            "model": model,
        })
    if not out:
        # 探测任务型异步响应（ModelScope Qwen-Image 等：返回 task_id+task_status，data=None）
        # 标准 OpenAI/硅基流动走 data[].b64_json|url；异步型需轮询结果，诚实暴露协议差异。
        ts = getattr(resp, "task_status", None)
        tid = getattr(resp, "task_id", None)
        if tid:
            raise GenerationError(
                "IMAGE_GENERATION_FAILED",
                f"生图返回异步任务（task_id={tid[:8]}… task_status={ts}）"
                f"且 data 为空：{model} 通过该端点走任务型协议，"
                f"本渲染器已接标准 OpenAI Images 协议（data[].b64_json/url），"
                f"请换用 OpenAI 兼容同步端点（如 OpenAI/硅基流动 images），或补充该供应商的任务轮询端点",
            )
        raise GenerationError(
            "IMAGE_GENERATION_FAILED",
            f"生图返回空结果（model={model}）；供应商未返回 b64_json/url",
        )
    return out


def _download_bytes(url: str) -> bytes:
    import urllib.request
    with urllib.request.urlopen(url, timeout=120) as resp:
        return resp.read()


@dataclass(frozen=True)
class GenerationResult:
    asset_id: str
    variant: str
    type: str
    file: str
    width: int
    height: int
    input_fingerprint: str
    output_fingerprint: str
    provider_id: str


def _build_single_scene_program(prompt: str, variant: str, width: int, height: int):
    """prompt → 单场景 GraphicProgram（确定性，变化只在 style/强调色）。"""
    from video_director.graphics import director as vd_director

    style_map = {"A": "editorial", "B": "minimal", "C": "bold"}
    plan = vd_director.build_graphic_plan(
        script=prompt,
        content_type="knowledge",
        style_id=style_map.get(variant, "editorial"),
        scene_duration=3.0,
        width=width,
        height=height,
        fps=30.0,
    )
    # 单场景版本：只取首场景（生成素材是一个视觉卡，非完整叙事）
    plan["scenes"] = plan["scenes"][:1]
    if plan["scenes"]:
        plan["scenes"][0]["duration"] = min(plan["scenes"][0]["duration"], 3.0)
    return vd_director.instantiate_graphic_program(plan)


def _manifest_path(project_id: str) -> Path:
    return config.project_dir(project_id) / "assets" / "manifest.json"


def _load_manifest(project_id: str) -> List[dict]:
    from video_director.project_manifest import read_project_manifest
    return read_project_manifest(project_id)


def _save_manifest(project_id: str, additions: List[dict]) -> None:
    from video_director.project_manifest import merge_project_manifest_entries
    merge_project_manifest_entries(project_id, additions, reject_conflicting_ids=True)


def generate(
    project_id: str,
    prompt: str,
    gen_type: str = "image",
    width: int = 1080,
    height: int = 1920,
    variant: Optional[str] = None,
    model: Optional[str] = None,
    reference: Optional[str] = None,
    first_frame: Optional[str] = None,
    last_frame: Optional[str] = None,
) -> List[GenerationResult]:
    """生成 1 或 3 个素材变体，落地到项目 assets 并登记 lineage。

    variant=None → 生成 A/B/C 三变体（§31）；指定则只生成该变体。
    model：显式模型覆盖（§38 Model 选择器；None 用角色配置）。
    reference：引用风格/素材标记（§17 reference-focused；当前接入 prompt 上下文
    与 graphic style 分流，资产引用待 Reference Graph 扩展）。
    first_frame / last_frame（§29 GenerationRequest）：视频生成的首帧/末帧
    引用。当前 provider（OpenAI 兼容 / ModelScope）协议支持有限——首末帧
    会进入 input_fingerprint（§39 CacheKey 语义）并诚实记录到 lineage，
    但若 provider 不支持则生成时明确报错而非静默忽略。
    provider 分流（§五十一 Capability G）：
        image   → roles["image"] AI 生图；无 key 兜底 Graphics Runtime 程序化
        graphic → Graphics Runtime 程序化（自有 renderer，确定性）
        video   → roles["video"] AI 生视频（异步任务）；无 key 诚实报错（不 fake）
        motion  → Huashu Procedural Motion（§十八 艺术动画，确定性）
        sfx     → GENERATION_UNAVAILABLE
    """
    if not prompt or not str(prompt).strip():
        raise GenerationError("INVALID_PROMPT", "生成提示词不能为空")
    if gen_type not in SUPPORTED_TYPES:
        raise GenerationError(
            "GENERATION_UNAVAILABLE",
            f"类型 {gen_type!r} 无可用生成 provider（可用: image/graphic/video/motion）",
        )
    prompt = str(prompt).strip()
    # §17/§38 引用接入：reference 风格 → 进 prompt 上下文 + graphic style 分流；
    # §90 CAP-GEN-007/008 参考图/参考视频：asset:<id> 形式把项目素材路径进 prompt
    # 上下文（视觉参考描述；生视频 image-to-video 协议接入后可升级为首帧输入）。
    if reference and reference.startswith("style:"):
        style = reference.split(":", 1)[1]
        prompt = f"{prompt}（风格：{style}）"
        _GEN_STYLE_OVERRIDE = style
    elif reference and reference.startswith("asset:"):
        ref_asset_id = reference.split(":", 1)[1]
        ref_manifest = _load_manifest(project_id)
        ref_entry = next((m for m in ref_manifest if m.get("id") == ref_asset_id), None)
        if ref_entry is None:
            raise GenerationError("REFERENCE_NOT_FOUND",
                                  f"参考素材 {ref_asset_id!r} 不在项目 manifest（§90）")
        ref_desc = ref_entry.get("description") or ref_entry.get("prompt") or Path(ref_entry.get("file", "")).stem
        prompt = f"{prompt}（参考画面：{ref_desc}）"
        _GEN_STYLE_OVERRIDE = None
    else:
        _GEN_STYLE_OVERRIDE = None
    variants = [variant] if variant else list(VARIANTS)
    base = config.project_dir(project_id)
    assets_dir = base / "assets" / "generated"
    assets_dir.mkdir(parents=True, exist_ok=True)

    # §39 CacheKey + InputFingerprint：prompt + type + 引用 + 首末帧 + providerVersion 都进指纹
    fp_src = prompt + "|" + gen_type
    if reference:
        fp_src += "|ref:" + reference
    if first_frame:
        fp_src += "|first:" + str(first_frame)[:80]
    if last_frame:
        fp_src += "|last:" + str(last_frame)[:80]
    if model:
        fp_src += "|model:" + str(model)
    input_fp = hashlib.sha256(fp_src.encode("utf-8")).hexdigest()[:16]
    results: List[GenerationResult] = []
    manifest = _load_manifest(project_id)
    manifest_ids_before_generation = {
        item.get("id") for item in manifest if isinstance(item.get("id"), str)
    }

    if gen_type == "image":
        results = _generate_image_or_fallback(
            project_id, prompt, width, height, variants, assets_dir, input_fp, manifest,
            model=model)
    elif gen_type == "video":
        # 审计差距 2：视频分支入口先恢复历史 running 任务（幂等；失败不阻塞
        # 本次生成——恢复失败只记警告，job 留待下次）。
        try:
            recovered = recover_pending_video_jobs(project_id)
            if recovered:
                logger.info(
                    f"generation: 恢复 {len(recovered)} 个历史视频任务: "
                    f"{[(j['job_id'][:16], j['status']) for j in recovered]}")
        except Exception as exc:  # noqa: BLE001 恢复失败不阻塞新生成
            logger.warning(f"generation: 视频任务恢复失败（不阻塞）: {exc}")
        # §29：首末帧先问 Provider Capability Matrix，不再由生成器自己猜协议。
        if first_frame or last_frame:
            try:
                from video_director import provider_matrix
                provider_matrix.require("video", "video.image_to_video",
                                       _role_cfg("video"))
            except provider_matrix.ProviderCapabilityError as exc:
                raise GenerationError(
                    "FIRST_FRAME_UNSUPPORTED" if exc.state.status == provider_matrix.UNSUPPORTED
                    else "VIDEO_PROVIDER_UNAVAILABLE",
                    str(exc),
                ) from exc
        results = _generate_video(
            project_id, prompt, width, height, variants, assets_dir, input_fp, manifest,
            model=model)
    elif gen_type == "motion":
        # §十八/§十四 Huashu 艺术动画：生成入口统一走 generation（与图片/视频
        # 同一 UI 面），产物由 procedural_motion 持久化（plans/motion + Asset
        # Catalog）——确定性单产物，variant 概念退化为 grammar 维度。
        results = _generate_motion(
            project_id, prompt, width, height, input_fp, manifest, reference=reference)
    else:  # graphic → 程序化视觉卡（自有 renderer，确定性）
        results = _generate_graphic(
            prompt, width, height, variants, assets_dir, input_fp, manifest)

    # Save only this run's additions. The merge helper re-reads the current
    # manifest under project_lock, preserving uploads and concurrent jobs.
    manifest_additions = [
        item for item in manifest
        if isinstance(item.get("id"), str)
        and item.get("id") not in manifest_ids_before_generation
    ]
    _save_manifest(project_id, manifest_additions)
    if gen_type == "video":
        # Do not declare registration complete until the manifest merge is durable.
        for result in results:
            job = next((
                item for item in _load_jobs(project_id).values()
                if item.get("input_fingerprint") == result.input_fingerprint
                and item.get("variant") == result.variant
                and item.get("provider_task_id")
            ), None)
            if job:
                _update_job(project_id, job["job_id"], {
                    "manifest_registered": True,
                    "recovery_needs_registration": False,
                })
    # §39 生成产物入 Asset Registry（AI 图片/视频 = 普通 Asset：Library 全局可复用，
    # 与本地/Stock 无差别；失败不阻断生成主链——项目 manifest 已有）。
    try:
        from video_director import asset_catalog as _acat
        for r in results:
            if r.file and Path(r.file).is_file():
                _acat.register_generated(r.file, prompt=prompt[:120])
    except Exception as exc:  # noqa: BLE001 Library 登记失败不阻断生成
        logger.warning(f"generation: Library 登记失败（不阻断）: {exc}")
    logger.info(
        f"generation: {gen_type} x{len(results)} prompt={prompt[:30]!r} → {project_id}"
    )
    return results


def _register_result(
    manifest, results, asset_id, asset_type, out_file, width, height, dur,
    model, provider_id, prompt, input_fp, variant,
) -> None:
    out_fp = _fingerprint(Path(out_file).read_bytes())
    results.append(GenerationResult(
        asset_id=asset_id, variant=variant, type=asset_type, file=str(out_file),
        width=width, height=height,
        input_fingerprint=input_fp, output_fingerprint=out_fp,
        provider_id=provider_id,
    ))
    license_note = (
        f"generated by {provider_id}" + (f" (model={model})" if model else ""))
    manifest.append({
        "id": asset_id, "type": asset_type, "file": str(out_file),
        "duration": dur, "width": width, "height": height, "fps": 30.0,
        "source": "generated", "origin": "generated",
        "license_note": license_note, "prompt": prompt,
        "generation_request_id": f"genreq-{input_fp}",
        "provider_id": provider_id, "model": model,
        "input_fingerprint": input_fp, "output_fingerprint": out_fp,
        "variant": variant, "created_at": time.time(),
    })


def _generate_motion(project_id, prompt, width, height, input_fp, manifest,
                     reference=None):
    """motion → Huashu Procedural Motion（§十八 艺术动画）。

    reference = "grammar:y5_kinetic_type" 形式选语法；缺省 y5_kinetic_type。
    产物经 procedural_motion.create_motion_asset：plans/motion + artifact +
    Asset Catalog 全套持久化；此处只补 generation manifest 登记（§26 UI 闭环），
    不重复登记 Library（create_motion_asset 已做）。
    """
    from video_director import procedural_motion

    grammar = "y5_kinetic_type"
    if reference and reference.startswith("grammar:"):
        grammar = reference.split(":", 1)[1].strip() or grammar

    out = procedural_motion.create_motion_asset(project_id, {
        "grammar": grammar,
        "prompt": prompt,
        "duration": 5.0,
        "width": width,
        "height": height,
    })
    entry = out.get("asset") or {}
    program = out.get("program") or {}
    file_path = str(entry.get("file") or "")
    if not file_path or not Path(file_path).is_file():
        raise GenerationError("MOTION_RENDER_EMPTY", "Huashu 动画渲染产物缺失")
    out_fp = _fingerprint(Path(file_path).read_bytes())
    asset_id = str(entry.get("asset_id") or f"asset-{uuid.uuid4().hex[:8]}")
    results: List[GenerationResult] = []
    results.append(GenerationResult(
        asset_id=asset_id, variant="M", type="video", file=file_path,
        width=width, height=height,
        input_fingerprint=input_fp, output_fingerprint=out_fp,
        provider_id="huashu-motion",
    ))
    manifest.append({
        "id": asset_id, "type": "video", "file": file_path,
        "duration": float(program.get("duration") or 5.0),
        "width": width, "height": height, "fps": 30.0,
        "source": "generated", "origin": "huashu",
        "generator": "material.motion.create",
        "program_ref": str(program.get("id") or ""),
        "grammar": str(program.get("grammar") or grammar),
        "license_note": "generated by huashu-motion (src-huashu, CLEAR)",
        "prompt": prompt,
        "generation_request_id": f"genreq-{input_fp}",
        "provider_id": "huashu-motion", "model": None,
        "input_fingerprint": input_fp, "output_fingerprint": out_fp,
        "variant": "M", "created_at": time.time(),
    })
    return results


def _generate_graphic(prompt, width, height, variants, assets_dir, input_fp, manifest):
    """graphic → Graphics Runtime 程序化视觉卡（自有 renderer，确定性）。"""
    from video_director.graphics import compiler as vd_compiler
    from video_director.graphics import render as vd_gfx_render

    results: List[GenerationResult] = []
    for v in variants:
        program = _build_single_scene_program(prompt, v, width, height)
        html = vd_compiler.compile_to_html(program)
        asset_id = f"gen-{uuid.uuid4().hex[:8]}"
        out_file = assets_dir / f"{asset_id}.png"
        vd_gfx_render.render_still(html, str(out_file), width, height, frame=0)
        _register_result(manifest, results, asset_id, "image", out_file, width, height,
                         None, None, "graphics-runtime", prompt, input_fp, v)
    return results


def _generate_image_or_fallback(project_id, prompt, width, height, variants,
                                assets_dir, input_fp, manifest, model=None):
    """image → AI 生图（roles["image"]）；无 key/失败 → 兜底程序化视觉卡。"""
    results: List[GenerationResult] = []
    try:
        imgs = generate_image_variants(
            prompt, width, height, count=len(variants),
            variant=variants[0] if len(variants) == 1 else None,
            model_override=model)
        for i, item in enumerate(imgs):
            asset_id = f"gen-{uuid.uuid4().hex[:8]}"
            out_file = assets_dir / f"{asset_id}.png"
            out_file.write_bytes(item["data"])
            _register_result(manifest, results, asset_id, "image", out_file, width, height,
                             None, item["model"], "image-llm", prompt, input_fp,
                             item["variant"])
        if results:
            return results
        return _generate_graphic(prompt, width, height, variants, assets_dir,
                                 input_fp, manifest)
    except GenerationError as exc:
        if exc.code == "IMAGE_PROVIDER_UNAVAILABLE":
            logger.info(f"生图 provider 未配置，兜底程序化视觉卡: {exc}")
            return _generate_graphic(prompt, width, height, variants, assets_dir,
                                     input_fp, manifest)
        if exc.is_quota():
            # 配额耗尽不可自恢复：兜底程序化视觉卡保持可用，但必须明确提示用户
            # 充值或配置备用供应商（项目当前无第二供应商切换机制，见报告）
            logger.warning(
                f"生图配额耗尽，已兜底程序化视觉卡；请充值或配置备用供应商: {exc}"
            )
            return _generate_graphic(prompt, width, height, variants, assets_dir,
                                     input_fp, manifest)
        # IMAGE_GENERATION_FAILED：AI 生图失败 → 诚实兜底程序化，但记警告
        logger.warning(f"AI 生图失败，兜底程序化视觉卡: {exc}")
        return _generate_graphic(prompt, width, height, variants, assets_dir,
                                 input_fp, manifest)


def _generate_video(project_id, prompt, width, height, variants, assets_dir,
                    input_fp, manifest, model=None):
    """video → AI 生视频（roles["video"]，异步任务）；无 key 诚实报错（不 fake）。

    外部任务持久化（审计差距 2）：提交成功即落盘 provider_task_id 到
    generation_jobs.json；同 input_fingerprint+variant 的 running 任务直接
    复用轮询，不重复提交（防重复付费）。
    """
    # First recover already durable manifest rows. Replaying a completed
    # generation must not require a still-live provider API or task record.
    results: List[GenerationResult] = []
    pending_variants = []
    for v in variants:
        existing = next((
            item for item in manifest
            if item.get("input_fingerprint") == input_fp
            and item.get("variant") == v
            and item.get("type") == "video"
            and item.get("source") == "generated"
        ), None)
        if existing:
            existing_path = Path(existing.get("file") or "")
            if not existing_path.is_file() or existing_path.stat().st_size <= 0:
                raise GenerationError(
                    "VIDEO_MANIFEST_OUTPUT_MISSING",
                    "Manifest 已登记该视频，但本地文件缺失或为空；为避免重复付费，"
                    "系统不会自动创建新供应商任务。请先核对原任务记录与文件恢复情况。",
                )
            results.append(GenerationResult(
                asset_id=existing["id"], variant=v, type="video",
                file=str(existing_path), width=int(existing.get("width") or width),
                height=int(existing.get("height") or height),
                input_fingerprint=input_fp,
                output_fingerprint=existing.get("output_fingerprint") or
                    _fingerprint(existing_path.read_bytes()),
                provider_id=existing.get("provider_id") or "video-llm",
            ))
        else:
            pending_variants.append(v)
    if not pending_variants:
        return results

    cfg = _role_cfg("video")
    try:
        from video_director import provider_matrix
        provider_matrix.require("video", "video.generate", cfg)
    except provider_matrix.ProviderCapabilityError as exc:
        raise GenerationError(
            "VIDEO_PROVIDER_UNAVAILABLE" if exc.state.status == provider_matrix.MISSING
            else "CAPABILITY_UNSUPPORTED",
            str(exc),
        ) from exc
    if not (cfg or {}).get("api_key"):
        raise GenerationError(
            "VIDEO_PROVIDER_UNAVAILABLE",
            "生视频模型未配置（roles.video.api_key 为空）；请按供应商文档填写模型/端点",
        )
    model = model or cfg.get("model") or ""
    base_url = cfg.get("base_url") or ""
    if not model or not base_url:
        raise GenerationError(
            "VIDEO_PROVIDER_UNAVAILABLE",
            "生视频 model/base_url 未配置完整（供应商特定：Veo/Kling/Wan 兼容端点）",
        )
    client = _video_client(cfg)

    for v in pending_variants:
        asset_id = f"gen-{uuid.uuid4().hex[:8]}"
        out_file = assets_dir / f"{asset_id}.mp4"
        # A previous process may have atomically materialized the MP4 and marked
        # the job done, then crashed before manifest registration. Reuse that
        # verified atomic output directly; provider task records may expire and
        # must not be required to retrieve bytes that are already local.
        reusable = _find_reusable_job(project_id, input_fp, v)
        recovered_file = None
        if (reusable
                and reusable.get("status") == "done"
                and reusable.get("recovery_needs_registration") is True
                and reusable.get("output_commit_protocol") == "atomic-v1"):
            candidate = Path(reusable.get("output_file") or "")
            if candidate.is_file() and candidate.stat().st_size > 0:
                recovered_file = candidate
                out_file = candidate
        try:
            url = None if recovered_file else _create_poll_and_persist_video(
                client, project_id, model, prompt, width, height,
                input_fp, v, str(out_file))
        except GenerationError:
            # Preserve machine-readable safety states such as VIDEO_SUBMISSION_UNKNOWN.
            raise
        except Exception as exc:
            # 供应商拒绝/配额/网络故障 → 诚实暴露，不 fake。
            # 配额类（429/insufficient quota/欠费）自动归类 QUOTA_EXCEEDED。
            err = GenerationError(
                "VIDEO_GENERATION_FAILED",
                f"生视频调用失败（model={model}）：{exc}",
            )
            if err.is_quota():
                logger.warning(f"生视频配额耗尽，请充值或配置备用供应商: {err}")
            raise err from exc
        job = next((item for item in _load_jobs(project_id).values()
                    if item.get("input_fingerprint") == input_fp
                    and item.get("variant") == v
                    and item.get("provider_task_id")), None)
        if job is None:
            raise GenerationError(
                "VIDEO_JOB_MISSING",
                "供应商任务已返回视频地址，但未找到持久化任务记录；禁止静默登记成品",
            )
        if url:
            _persist_video_output(project_id, job["job_id"], str(out_file), url)
        elif not (out_file.is_file() and out_file.stat().st_size > 0):
            raise GenerationError(
                "VIDEO_RECOVERED_OUTPUT_MISSING",
                "恢复任务没有可用的本地 MP4；为避免重复付费，不会自动重新提交",
            )
        _register_result(manifest, results, asset_id, "video", out_file, width, height,
                         5.0, model, "video-llm", prompt, input_fp, v)
        # The outer generate() persists the manifest atomically before marking
        # this job registered. A crash between those operations is idempotent:
        # the next call finds the durable row above and repairs the marker.
    return results


def _create_poll_and_persist_video(
    client, project_id, model, prompt, width, height,
    input_fp: str, variant: str, out_file: str, timeout: float = 600.0,
) -> str:
    """提交 →（持久化）→ 轮询（每轮更新 jobs）→ 返回 mp4 url。

    提交前先查可复用的 running 任务（同 fingerprint+variant）；命中则跳过
    重新提交，直接接管轮询（防重复付费）。行为与原 _create_and_poll_video
    一致，仅叠加持久化。
    """
    import time as _time
    # Serialize the lookup/reservation/submit/persist boundary. A durable
    # "submitting" reservation is written before the non-idempotent provider
    # POST. If the process dies or the response times out after provider
    # acceptance, the next caller fails closed instead of charging again.
    with _video_submission_lock(project_id, input_fp, variant):
        unresolved = _find_unresolved_submission(project_id, input_fp, variant)
        if unresolved:
            raise GenerationError(
                "VIDEO_SUBMISSION_UNKNOWN",
                "相同素材请求存在结果不确定的供应商提交记录 "
                f"({unresolved.get('job_id')})；为避免重复付费，系统不会自动重提。"
                "请先核实供应商任务/账单，再人工处理该任务记录。",
            )

        reusable = _find_reusable_job(project_id, input_fp, variant)
        if reusable:
            job_id = reusable["job_id"]
            task_id = reusable["provider_task_id"]
            logger.info(f"generation: 复用 running 视频任务 {job_id}"
                        f"（fingerprint={input_fp[:12]}，不重复提交）")
        else:
            job_id = f"genjob-{uuid.uuid4().hex[:12]}"
            reservation = {
                "provider": model or "video-llm",
                "gen_type": "video",
                "prompt_fingerprint": _prompt_fingerprint(prompt, "video"),
                "input_fingerprint": input_fp,
                "variant": variant,
                "status": "submitting",
                "output_file": out_file,
            }
            _update_job(project_id, job_id, reservation)

            size = _image_size(width, height)
            try:
                try:
                    create = client.post(
                        "/video/generations",
                        json={"model": model, "prompt": prompt, "size": size},
                    )
                except Exception as exc:
                    # Only an explicit unsupported-route response proves that
                    # the first endpoint did not create a task. A timeout or
                    # connection error is ambiguous and must never trigger a
                    # second non-idempotent POST.
                    if getattr(exc, "status_code", None) not in (404, 405):
                        raise
                    create = client.post(
                        "/videos",
                        json={"model": model, "prompt": prompt, "size": size},
                    )
            except Exception as exc:
                _update_job(project_id, job_id, {
                    "status": "submission_unknown",
                    "submission_error_type": type(exc).__name__,
                    "submission_http_status": getattr(exc, "status_code", None),
                })
                raise

            data = create.json() if hasattr(create, "json") else create
            if isinstance(data, dict) and "data" in data:
                data = data["data"]
            task_id = None
            if isinstance(data, dict):
                task_id = data.get("id")
            elif isinstance(data, list) and data:
                task_id = data[0].get("id")
            if not task_id:
                _update_job(project_id, job_id, {
                    "status": "submission_unknown",
                    "submission_error_type": "MissingProviderTaskId",
                })
                raise GenerationError(
                    "VIDEO_SUBMISSION_UNKNOWN",
                    f"供应商响应未包含任务 ID；为避免重复付费，不会自动重提：{str(data)[:200]}",
                )

            # Provider task ID is durable before releasing the submission lock.
            _update_job(project_id, job_id, {
                "provider_task_id": task_id,
                "status": "running",
            })

    deadline = _time.time() + timeout
    while _time.time() < deadline:
        status, url, _raw = _query_video_status(client, task_id)
        if status == "done":
            if not url:
                raise GenerationError(
                    "VIDEO_GENERATION_FAILED", "生视频任务完成但无 url")
            _update_job(project_id, job_id,
                        {"status": "downloading", "output_file": out_file})
            return url
        if status == "failed":
            _update_job(project_id, job_id, {"status": "failed"})
            raise GenerationError("VIDEO_GENERATION_FAILED",
                                  f"生视频任务失败：{str(_raw)[:200]}")
        _update_job(project_id, job_id, {"status": "running"})
        _time.sleep(5.0)
    # Provider task remains live/ambiguous after our local wait expires.
    # Keep it recoverable; a timeout is not proof that generation failed.
    _update_job(project_id, job_id, {"status": "running"})
    raise GenerationError("VIDEO_GENERATION_TIMEOUT",
                          f"生视频任务仍在供应商侧运行（本地等待超过 {int(timeout)}s）；可由恢复器继续轮询")


def _query_video_status(client, task_id: str) -> tuple:
    """查一次外部视频任务状态（纯函数，提交/轮询/恢复共用同一协议视图）。

    Returns:
        (status, url, raw)：status ∈ {"running", "done", "failed"}；
        url 仅 done 时可能有值；raw 为原始响应 dict（诊断用）。
        终态失败不在此抛异常（恢复路径需区分「外部失败」与「查询失败」），
        由调用方决定 raise 还是落盘。
    """
    job = client.get(f"/video/generations/{task_id}")
    js = job.json() if hasattr(job, "json") else job
    if isinstance(js, dict) and "data" in js:
        js = js["data"]
    if isinstance(js, list) and js:
        js = js[0]
    status = (js.get("status") if isinstance(js, dict) else None)
    if status in ("completed", "succeeded", "done"):
        url = None
        if isinstance(js, dict):
            url = js.get("url") or js.get("output") or js.get("video_url")
        if isinstance(url, dict):
            url = url.get("url")
        if not url:
            raise GenerationError(
                "VIDEO_GENERATION_FAILED", f"生视频任务完成但无 url：{str(js)[:200]}")
        return "done", url, js
    if status in ("failed", "error", "cancelled"):
        return "failed", None, js
    return "running", None, js


def add_to_timeline(project_id: str, scene_id: str, asset_id: str) -> dict:
    """生成结果接入时间线（EDT-008 Add to Timeline）：走 editor 命令 generate_media。

    由 API 层决定 asset 类型；这里只做 asset 存在性 + 类型解析，
    实际落层复用 editor._cmd_generate_media（revision 版本化）。
    """
    manifest = _load_manifest(project_id)
    entry = next((a for a in manifest if a.get("id") == asset_id), None)
    if entry is None:
        raise GenerationError("ASSET_NOT_FOUND", f"生成素材 {asset_id} 未登记")
    from video_director import editor as vd_editor
    result = vd_editor.execute_command(project_id, {
        "command": "generate_media",
        "targetId": scene_id,
        "payload": {"assetId": asset_id, "assetType": entry.get("type", "image")},
        "author": "generation",
    })
    return {
        "status": result.status,
        "revision_id": result.revision_id,
        "composition_version": result.composition_version,
        "asset_type": entry.get("type"),
        "errors": result.errors,
    }