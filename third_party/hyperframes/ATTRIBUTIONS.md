# ATTRIBUTIONS

本仓库若引用第三方组件，须在此登记归属。当前状态：

## HyperFrames（heyge-com/hyperframes）

- License：Apache-2.0
- 吸收方式：**架构思想 + 协议契约吸收**，未复制源码入本仓库
- 归属声明：本系统的 Graphics Runtime（`video_director/graphics/`）的
  FrameAdapter 契约、有理 FPS 协议、确定性预检清单、沙箱策略，
  借鉴自 HyperFrames 项目的公开设计（Apache-2.0）。

## 无其他第三方运行时依赖

本系统的 Graphics Runtime 依赖列表：
- Playwright（Chromium 帧捕获，测试与渲染共用）
- FFmpeg（随包二进制，见 `pack_release.py`）

不依赖：GSAP / Lottie / Three.js / Anime.js / Remotion。