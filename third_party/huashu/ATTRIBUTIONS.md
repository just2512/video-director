# Huashu Motion attribution

来源仓库：alchaincyf/huashu-art-motion
固定来源提交：707b920dca7308239ae65306629cb5a2cf7f4d16
许可：仓库代码与文档主体为 MIT License。

本仓库只吸收参数化解说动画所需的 JavaScript 运行代码，并经过离线确定性适配：
- 删除运行时 XHR / fetch 加载；
- 不把 Huashu 的 render.py、HTTP server 或独立 FFmpeg 作为生产入口；
- 生产渲染统一进入 Video Director Graphics Renderer；
- Huashu 原仓库中的字体、角色、美术展示素材不作为本项目生产资产随意复用。

原仓库另有独立授权边界的字体、笔顺数据及作者角色示范素材，使用时仍以其各自许可证为准。
