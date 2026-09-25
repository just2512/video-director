"""Registry 体系包（Video Shotcraft 商业集成方案：REG-001~REG-005）。

四层分离（方案 §47 最终 Capability Map）：
    Registry = 知识定义（只读文件 + schema 校验 + 四态商业状态）
    Resolver = 意图 → 候选（SUPPORTED/MISSING_REQUIREMENT/INCOMPATIBLE，无评分）
    Composition = 实例（Scene/Layer 带 origin lineage）
    Renderer = 执行（Registry 禁止出现任何 renderer 代码）

商业安全边界（方案 §四十一/全局契约 §14-16）：
    CLEAR/REVIEW/BLOCKED/UNKNOWN 四态贯穿 Registry → Preflight → Publish；
    Apache-2.0 代码 ≠ 第三方素材授权 ≠ Remotion 授权，provenance 独立记录。
"""
