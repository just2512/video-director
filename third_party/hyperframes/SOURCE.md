# HyperFrames 参考记录（SOURCE）

## 参考项目

- 名称：HyperFrames（heyge-com / hyperframes）
- 仓库：https://github.com/heygen-com/hyperframes
- 本地 checkout：`D:\autocut\hyperframes-main`（无 .git 元数据的目录拷贝）
- 版本锚点：**commit=UNKNOWN**（checkout 不含可确认的 commit 元数据，不臆造版本号）

## 许可证

仓库标注 **Apache-2.0**（见仓库 LICENSE）。

## 吸收边界（方案 §45/§46/§84）

我们吸收的是**架构思想与协议契约**，不是代码：

| 吸收项 | 方案节 | 处理方式 |
|---|---|---|
| `FrameAdapter`（init/getDurationFrames/seekFrame/destroy）契约 | §9 | 在 `video_director/graphics/` 自实现（Python），不复制 TS 源码 |
| 有理 FPS（numerator/denominator）协议 | §7 | 自实现 `graphics/contract.py` |
| `Frame 是唯一时间事实`（禁 Date.now/Math.random） | §7/§50 | 自实现 `graphics/preflight.py` 校验 |
| 运行时协议 capability 元数据（protocolVersion/fps/capabilities） | §7 | 自实现 `graphics/contract.py` |
| Block 与 Component 分离的思想 | §17 | 自实现 `graphics/blocks.py`（Block）+ 后续 Component |
| Registry / Catalog 思想（能力可搜索、三态、零评分） | §15-18 | 复用已有 registries 体系 |
| 沙箱/隔离 origin 安全经验 | §48/§49 | 自实现 `graphics/sandbox.py` |
| Producer 的 HTML→BeginFrame 捕获→FFmpeg 编码流程 | §6 | 自实现 `graphics/render.py`（Playwright + ffmpeg） |

## 明确不吸收（商业安全）

- 不引入 `@hyperframes/core`、`@hyperframes/engine`、`@hyperframes/producer` 作为运行时依赖
- 不引入 GSAP / Lottie / Three.js / Anime.js / WAAPI 第三方动画运行时为本系统依赖
- 不引入参考仓库内的第三方字体、音频、品牌、演示资产
- 不把 HyperFrames 的 HTML 命名（data-*）原样当上层 Composition Contract

## 版本记录

见同目录 `version.json`。