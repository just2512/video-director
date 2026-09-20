"""Checkpoint Store（OpenMontage 思想：阶段 checkpoint + 上游指纹失效重算）。

零外部依赖（stdlib only），JSON 文件落盘：
    storage/composer/<project_id>/plans/checkpoints.json

回答"上游变了才重算下游"：每个 stage 完成时记录其输入指纹（上游 stage 的
hash 集合），下次运行时对比指纹——任一上游指纹变化则该 stage 需要重算。

典型场景（换风格只重算 style 下游）：调用方只传 {style: hash} 给
stage_upstream_changed，style 之前的 stage（script/tts/alignment/subtitle）
指纹不在对比集合内，不触发重算。

数据结构（checkpoints.json）：
    {
      "project_id": "...",
      "checkpoints": { "<stage_name>": {当前记录} },
      "history": { "<stage_name>": [旧记录...] }
    }
旧记录进入 history 不覆盖（审计可追溯，回答"当时为什么能生成"）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

from video_director import config

#: checkpoint 文件名（放项目 plans/ 子目录）
CHECKPOINT_FILENAME = "checkpoints.json"

#: 合法 stage 状态
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


def _checkpoint_path(project_id: str) -> Path:
    """storage/composer/<project_id>/plans/checkpoints.json。"""
    return config.project_dir(project_id) / "plans" / CHECKPOINT_FILENAME


def _empty_store(project_id: str) -> dict:
    return {"project_id": project_id, "checkpoints": {}, "history": {}}


class CheckpointStore:
    """阶段 checkpoint 存储（JSON 文件，轻量、零外部依赖）。

    用法：
        store = CheckpointStore()
        store.record_stage(pid, "tts", "completed",
                           {"script": "sha256:abc1"},
                           ["storage/composer/p1/audio/narration.wav"])
        store.stage_upstream_changed(pid, "tts", {"script": "sha256:abc1"})  # False
        store.stage_upstream_changed(pid, "tts", {"script": "sha256:xyz9"})  # True

    每个方法调用都重新读盘（无进程内缓存），跨会话/跨进程一致。
    """

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    def _load(self, project_id: str) -> dict:
        path = _checkpoint_path(project_id)
        if not path.is_file():
            return _empty_store(project_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(f"checkpoint 文件损坏，重建空 store: {path} ({exc})")
            return _empty_store(project_id)
        if not isinstance(data, dict) or "checkpoints" not in data:
            logger.warning(f"checkpoint 文件结构异常，重建空 store: {path}")
            return _empty_store(project_id)
        data.setdefault("history", {})
        data.setdefault("checkpoints", {})
        return data

    def _save(self, project_id: str, data: dict) -> None:
        path = _checkpoint_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def get_stage(self, project_id: str, stage_name: str) -> Optional[dict]:
        """取某 stage 当前 checkpoint 记录（无则 None）。"""
        data = self._load(project_id)
        record = data["checkpoints"].get(stage_name)
        return dict(record) if record else None

    def get_history(self, project_id: str, stage_name: str) -> List[dict]:
        """取某 stage 的历史记录列表（旧 checkpoint，不覆盖）。"""
        data = self._load(project_id)
        return list(data.get("history", {}).get(stage_name, []))

    def list_stages(self, project_id: str) -> List[str]:
        """已有 checkpoint 的 stage 名列表。"""
        data = self._load(project_id)
        return list(data["checkpoints"].keys())

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def record_stage(
        self,
        project_id: str,
        stage_name: str,
        status: str,
        fingerprints: Optional[Dict[str, str]] = None,
        artifact_paths: Optional[List[str]] = None,
    ) -> dict:
        """记录一次 stage checkpoint。

        status: in_progress | completed | failed
        fingerprints: {上游stage名: hash}——本 stage 的输入指纹
        artifact_paths: 本阶段产物路径列表

        已有当前记录时先移入 history（审计不覆盖），再写新当前记录。
        返回写入的记录 dict。
        """
        if status not in (STATUS_IN_PROGRESS, STATUS_COMPLETED, STATUS_FAILED):
            raise ValueError(f"非法 status: {status}（合法: in_progress/completed/failed）")
        data = self._load(project_id)

        record = {
            "stage": stage_name,
            "status": status,
            "fingerprints": dict(fingerprints or {}),
            "artifact_paths": list(artifact_paths or []),
            "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "recorded_ts": time.time(),
        }

        previous = data["checkpoints"].get(stage_name)
        if previous is not None:
            history = data["history"].setdefault(stage_name, [])
            history.append(previous)

        data["checkpoints"][stage_name] = record
        self._save(project_id, data)
        logger.info(
            f"checkpoint [{stage_name}] {status} "
            f"(fingerprints={list(record['fingerprints'].keys())}, "
            f"artifacts={len(record['artifact_paths'])})"
        )
        return record

    # ------------------------------------------------------------------
    # 失效判断
    # ------------------------------------------------------------------

    def stage_upstream_changed(
        self,
        project_id: str,
        stage_name: str,
        upstream_fingerprints: Dict[str, str],
    ) -> bool:
        """判断某 stage 是否需要重算：输入的 upstream 指纹与已存 checkpoint 对比。

        upstream_fingerprints: {上游stage名: hash}
        返回 True 当：
          - 该 stage 无 checkpoint 记录（从未跑过 → 必须跑）
          - 任一上游指纹与已存指纹不同
        返回 False 当：该 stage 有 completed 记录，且调用方给的指纹集合内
          所有上游指纹与已存值一致。
        调用方传「哪些 stage 的输入指纹」，checkpoint 据此判断——传的集合
        之外的指纹不参与对比（换风格只重算 style 下游的实现基础）。

        上游 stage 在已存记录中无指纹时（新调用方引入新上游），视为变化 → True。
        """
        data = self._load(project_id)
        record = data["checkpoints"].get(stage_name)
        if record is None:
            logger.info(f"checkpoint [{stage_name}] 无记录 → 需要执行")
            return True
        if record.get("status") != STATUS_COMPLETED:
            logger.info(f"checkpoint [{stage_name}] status={record.get('status')} → 需要重算")
            return True
        stored = record.get("fingerprints") or {}
        for up_name, up_hash in upstream_fingerprints.items():
            stored_hash = stored.get(up_name)
            if stored_hash != up_hash:
                logger.info(
                    f"checkpoint [{stage_name}] 上游 {up_name} 指纹变化 "
                    f"({stored_hash} → {up_hash}) → 需要重算"
                )
                return True
        logger.debug(f"checkpoint [{stage_name}] 指纹一致 → 跳过重算")
        return False

    # ------------------------------------------------------------------
    # 便捷辅助
    # ------------------------------------------------------------------

    def invalidate_stage(self, project_id: str, stage_name: str) -> bool:
        """显式失效某 stage（强制下次重算）。实现：删除当前记录（进 history）。"""
        data = self._load(project_id)
        previous = data["checkpoints"].pop(stage_name, None)
        if previous is None:
            return False
        data["history"].setdefault(stage_name, []).append(previous)
        self._save(project_id, data)
        logger.info(f"checkpoint [{stage_name}] 已显式失效（进 history）")
        return True

    def invalidate_downstream(self, project_id: str, stage_name: str,
                              pipeline_stages: List[str]) -> List[str]:
        """按流水线顺序失效某 stage 及其之后所有 stage（上游变了 → 下游重算）。

        pipeline_stages: 流水线 stage 名列表（来自 pipeline_defs.get_pipeline()
        的 stage_names()）。按列表顺序找到 stage_name 位置，失效它及其后全部。
        返回被失效的 stage 名列表。
        """
        if stage_name not in pipeline_stages:
            raise ValueError(f"stage {stage_name} 不在流水线中（流水线: {pipeline_stages}）")
        idx = pipeline_stages.index(stage_name)
        downstream = pipeline_stages[idx:]
        invalidated = []
        for name in downstream:
            if self.invalidate_stage(project_id, name):
                invalidated.append(name)
        logger.info(f"invalidate_downstream({stage_name}): {invalidated or '无已存记录'}")
        return invalidated
