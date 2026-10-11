"""创作管线的版本化数据契约。字段存在不代表渲染能力已实现。"""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator

TimingSource = Literal[
    "tts_native", "asr_native", "word_interpolation",
    "text_interpolation", "manual", "missing",
]
TimingStatus = Literal["ALIGNED", "PARTIAL", "MISSING"]
BeatWeight = Literal["main", "sub"]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class CharacterTiming(ContractModel):
    """可定位的口播字符；时间均为音频绝对毫秒。"""
    index: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=8)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    source: TimingSource
    review_required: bool = False

    @model_validator(mode="after")
    def _valid_interval(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("字符时间必须满足 end_ms > start_ms")
        return self


class SentenceTiming(ContractModel):
    sentence_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    source: Literal["tts_word_boundary", "asr", "manual", "missing"]
    status: TimingStatus
    characters: list[CharacterTiming] = Field(default_factory=list)
    review_reasons: list[str] = Field(default_factory=list)
    reviewed: bool = False  # 人工抽听后显式确认，保留插值来源而不伪装为原生锚点

    @model_validator(mode="after")
    def _valid_sentence(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("句子时间必须满足 end_ms > start_ms")
        previous_index = -1
        previous_start = -1
        for char in self.characters:
            if char.index <= previous_index:
                raise ValueError("characters 必须按原文 index 严格递增")
            if char.start_ms < self.start_ms - 100 or char.end_ms > self.end_ms + 100:
                raise ValueError("字符时间超出句子时间窗")
            if char.start_ms < previous_start:
                raise ValueError("字符时间不能倒序")
            previous_index = char.index
            previous_start = char.start_ms
        if self.status == "MISSING" and self.characters:
            raise ValueError("MISSING 时间状态不能包含伪造的字符锚点")
        if self.status != "MISSING" and not self.characters:
            raise ValueError("有时间状态的句子必须包含字符锚点")
        return self


class SemanticBeat(ContractModel):
    """语义由人工或导演确认；不从文本自动伪造语义标签。"""
    id: str = Field(min_length=1)
    sentence_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    intent: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    visual_need: list[str] = Field(default_factory=list)
    weight: BeatWeight = "main"
    shot_id: Optional[str] = None
    timing_status: TimingStatus = "MISSING"

    @model_validator(mode="after")
    def _valid_beat(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("语义拍时间必须满足 end_ms > start_ms")
        for field_name in ("intent", "entities", "visual_need"):
            if any(not value.strip() for value in getattr(self, field_name)):
                raise ValueError(f"{field_name} 不能包含空值")
        return self


class ShotLayerPlan(ContractModel):
    """镜头视觉层；引用现有配方或真实资产 ID。"""
    role: str = Field(min_length=1, max_length=80)
    recipe_id: Optional[str] = None
    asset_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    required: bool = True


class ShotPlan(ContractModel):
    shot_id: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    intent: str = ""
    beat_ids: list[str] = Field(default_factory=list)
    layers: list[ShotLayerPlan] = Field(default_factory=list)
    transition_id: Optional[str] = None
    unresolved_items: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _valid_shot(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("镜头时间必须满足 end_ms > start_ms")
        if len(self.beat_ids) != len(set(self.beat_ids)):
            raise ValueError("同一镜头不能重复引用同一个 beat_id")
        return self


class RecipeContract(ContractModel):
    """现有 Registry 的标准化描述；不能替代真实渲染能力检查。"""
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    semantic_intents: list[str] = Field(default_factory=list)
    compatible_layers: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    renderer: Literal["ffmpeg", "graphics_runtime", "remotion", "unknown"] = "unknown"
    implementation_ref: Optional[str] = None
    provenance_ref: Optional[str] = None


class CreativePlan(ContractModel):
    schema_version: Literal[1] = 1
    project_id: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    script_hash: str = Field(min_length=1)
    audio_hash: str = ""
    timing_source: Literal["tts_word_boundary", "asr", "missing"] = "missing"
    character_timings: list[SentenceTiming] = Field(default_factory=list)
    semantic_beats: list[SemanticBeat] = Field(default_factory=list)
    shots: list[ShotPlan] = Field(default_factory=list)
    updated_at: str = ""

    @model_validator(mode="after")
    def _unique_ids(self):
        for values, name, getter in (
            (self.character_timings, "sentence timing", lambda x: x.sentence_id),
            (self.semantic_beats, "semantic beat", lambda x: x.id),
            (self.shots, "shot", lambda x: x.shot_id),
        ):
            ids = [getter(item) for item in values]
            if len(ids) != len(set(ids)):
                raise ValueError(f"{name} ID 必须唯一")
        return self
