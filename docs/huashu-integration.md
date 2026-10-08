# Huashu Procedural Motion 接入

## 目标

把 huashu-art-motion 变成 Video Director 的一个 **Procedural Motion Provider**，而不是第二套编辑器或第二套渲染系统。

运行链：

`material.motion.create`
→ `MotionProgram`
→ Huashu grammar registry
→ 自包含 deterministic HTML
→ 现有 `graphics.render_graphics()`
→ MP4
→ Asset Catalog

## 已固定的 8 个 grammar

`y1_kurzgesagt`、`y2_vox`、`y3_whiteboard`、`y4_storytime`、`y5_kinetic_type`、`t1_3b1b`、`t2_keynote_ui`、`t3_finance_chart`。

AI 只能产生 grammar + duration + cues + data + asset references，不能产生 JavaScript。

## 为什么不用 Huashu 的 render.py

Huashu 原始 render.py 通过本地 HTTP + XHR 动态加载 grammar，并维护自己的逐帧 FFmpeg 入口。Video Director 已经拥有统一的 deterministic Graphics Runtime，因此生产环境只保留一套渲染事实源。

适配后的 bundle 在构建时固定进仓库；运行时禁止网络、墙钟和随机源。

## Artifact 持久化

每次生成同时写入：

- `plans/motion/<program>.json`
- `assets/generated/motion/<program>/motion.mp4`
- `assets/generated/motion/<program>/artifact.json`
- Asset Catalog

因此 WorkflowRun/重开/重试不需要依赖进程内存中的临时 motion state。

## 商业边界

Huashu 主体代码和文档为 MIT，但其仓库内字体、笔顺数据和作者角色示范素材存在独立许可。生产 bundle 不携带这些示范二进制资产。
