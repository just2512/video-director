# OpenFilm 吸收落地差距与 Video Director 目标架构

## 结论

目标不是把 OpenFilm 的代码逐文件复制进来，而是吸收它的**工程契约**：可读的影片文件、稳定 ID、文件系统作为 agent 与 Studio 的连接层、可验证的时间函数、按 ID 操作、确定性预览/导出。Video Director 已经有 FilmDocument、`film.html`、场景 `frame(t)` 契约、版本化操作、undo/redo、外部文件 watcher、AI 提案预览/确认和渲染验收，不能再另建一套平行影片模型。

最重要的差距不是“还少几个按钮”，而是：**导演创作流程仍分布在 CompositionIR 工作台与 Film Studio 之间；Studio 是渐进式 React/原生 DOM 混合；通用属性动画和若干专业剪辑交互仍不完整；frame(t) 的确定性范围小于任意网页；AI 目前提供操作提案与生命周期事件，不承诺模型 token 逐字流式输出或多人 CRDT 协作。**

## 目前已有的可复用基础

- **唯一最终时间线**：有效的 `film.html` 是最终编辑/渲染权威；初始化迁移是显式操作，不允许 CompositionIR 静默覆盖 FilmDocument。
- **文件化协作**：项目文件、agent CLI 与 Studio watcher 连接外部编辑；稳定快照经解析后发 revision 事件，坏文档会冻结编辑而不是被 Studio 覆盖。
- **冲突保护**：编辑操作绑定 revision 和 clip ID；旧 revision 的 AI 提案拒绝应用；缺失 ID 不会改投另一个片段。人机共享的是文本文件与可审计操作，不是聊天上下文中的隐藏状态。
- **编辑/渲染闭环**：多轨、轨道锁定/隐藏/静音、剪切、修剪、插入编辑、ripple delete、slip/roll/slide（有意限制适用条件）、愈合切口、淡入淡出、音量包络、波形、缩略图、撤销/重做和基于真实浏览器的 look/render 均已有实现。
- **任何编程智能体的入口**：`docs/FILM_AGENT_MANUAL.md` 和 `python -m video_director film ...` 已提供通用工作约定；不需要把 Codex、Claude Code 或其他 agent 的进程硬塞进 Studio。agent 可在项目工作目录读写场景/影片文件并使用相同 CLI 校验。

## 与 OpenFilm 指纹级对照的真实差距

| 维度 | 当前状态 | 必须补齐的语义 |
|---|---|---|
| 影片文档 | **已具备主要骨架**；HTML/轨道/片段/ID 与可编辑 overrides 已落地 | 固化版本化格式契约与迁移门禁；未知能力明确 warning/reject，不能“看似转成功” |
| 文件协作 | **已部分实现**：稳定快照 watcher、SSE、revision 检查、外部修改历史 | 明确区分 Studio 操作、CLI 操作、原始文件改写；在 UI 显示外部变更和操作来源；不宣称多人并发 CRDT |
| React Studio | **混合架构**：部分工具栏、Inspector、资产、AI 与时间线表面由 React 承载，指针手势和主状态桥仍在原生 JS | 持续迁移组件所有权，不做“一次性全重写”；同一控件不得同时由 React 与静态实现双重渲染/绑定 |
| 页面元素编辑 | 已支持 selector、文本、位置/尺寸、文字色、旋转；后端还支持背景色、不透明度、字重、对齐等受限字段 | Inspector 应暴露常用可编辑字段，并让 React 与无 bundle fallback 同契约；编辑不应意外物化未改字段 |
| 剪辑数学 | 已具备多个 source-aware 操作，但部分 roll/slide 条件严格；时间仍以秒浮点在许多接口流转 | 把帧吸附收敛到统一边界；长期评估整数 Tick/采样单位，而非全库冒险替换浮点 |
| 动画 | 音量包络可编辑，部分 scene channel 可由 frame(t) 驱动；fade 仍是线性两端淡化 | 通用属性关键帧轨道、可见关键帧 UI、Bezier/easing 手柄、逐帧一致的预览/导出，是完整 NLE 的主要缺口 |
| 实时 AI | Studio 的修改提案可预览/接受/拒绝，外部文件修改可热刷新 | 让自然语言可明确指向选中片段/场景/时间区间；显示计划、变更预览、校验结果和撤销入口。模型调用当前是阻塞生成，不要称 token 流式输出 |
| 确定性与导出 | `frame(t)`、本地资源边界、look 重复抽样、Revision 绑定导出均已有；采样检查不是全帧证明 | 对每个场景验证资源 ready 和帧回执；导出错误定位到 clip ID；明确运行时支持边界，不宣称任意网页/任意 WebGL 都确定 |
| 产品入口 | 旧导演工作台与全屏 Film Studio 仍是两个工作上下文；初始化迁移后才由 `film.html` 接管 | 收敛为一个项目壳：导演规划、素材、场景、剪辑、AI 对话与导出围绕同一项目/同一最终时间线；迁移只是项目状态变化，不再像切换到另一款应用 |

## 目标架构：不新增第二个“影片真相”

```text
用户 / Codex / Claude Code / 其他 coding agent
                  │
          MANUAL + CLI / MCP 适配器
                  │
         film.html + scenes/ + assets/
        （稳定 ID、revision、可读文本）
                  │
       FilmDocument / typed operations
       ├─ strict parse / source-span patch
       ├─ ID-based edit / lock / revision
       ├─ undo / redo / external change history
       └─ capability validation / migration warnings
                  │
     ┌────────────┴─────────────┐
     │                          │
  Studio UI                  Render runtime
  React surfaces             frame(t) / readiness
  native pointer adapter     offline resource guard
  viewer/timeline            look / deterministic sample
  Inspector/AI dock          FFmpeg mux + revision-bound export
     │                          │
     └────── watcher / SSE / same document ───────┘
```

**边界原则：**

1. 文件是 agent 与 Studio 之间的防腐层；聊天只传意图和短引用，不承载最终状态。
2. 所有 Studio 写操作最终转换为版本化、带稳定 ID 的 FilmOperation；原始 HTML 写入先写临时文件、校验，再原子替换。
3. `CompositionIR` 继续承担导演规划/迁移输入，但一旦 FilmDocument 生效，任何旧写入和旧渲染必须被硬拒绝；不能维持双向“尽力同步”。
4. 场景作者可以使用 HTML/CSS/SVG/Canvas/WebGL 等技术，但最终渲染需满足 `frame(t)`、资源 ready 和实际帧回执；不满足就显式报告，不静默输出半成品。
5. 不为复刻 OpenFilm 而把 ACP 进程管理复制进来。当前用户的 Codex/Claude Code 就是宿主 agent；标准文件格式 + CLI 才是跨 agent 的最小公分母，MCP 是适配器，不是新状态源。

## 前端剪映级能力的实施顺序

### A. 先统一导演与剪辑工作台

- 同一项目壳内保留脚本/分镜/素材池、预览、时间线、属性 Inspector、AI 对话、导出任务；面板可拖动调整，并持久化布局。
- 右侧不应在“AI 对话”和“属性检查器”之间强制二选一。默认布局可以是可调的上下/左右 dock；窄屏再折叠为 tab。
- AI 修改必须呈现目标片段、base revision、操作摘要和预览；接受后同一事务写入 FilmDocument，一次手势/一次 AI 批次对应一个 undo 单元。

### B. 补专业时间线基础交互

- 以纯函数统一时间换算、帧吸附、剪辑边界吸附和 source range 运算；拖动过程中只画 preview/ghost，pointerup 才提交一次事务。
- 已实现同项目多片段 Ctrl/⌘+C、Ctrl/⌘+V 和右键“重复”：保留片段相对布局与属性，在原轨道寻找可证明不重叠的区间，并作为一个 FilmDocument 事务提交；跨项目粘贴、覆盖式粘贴和拖入覆盖仍不支持。
- 时间线吸附现已覆盖片头、播放头与其他片段入/出点，按 8 个屏幕像素阈值计算；拖动期间显示吸附参考线，移动、修剪、滚动修剪和滑移式修剪的预览与提交都重新计算同一吸附目标。工具栏可关闭/启用吸附；右键菜单提供复制、重复、粘贴、切分、删除、波纹删除和打开属性检查器。
- 仍需补齐 Ctrl/触控板缩放的指针锚点一致性和跨轨移动时对多轨同步关系的更完整预览。
- 清楚区分 Lift（留下空隙）与 Ripple Delete（关闭所有相关轨道上的空隙）；插入编辑仅在参与轨道/sync-lock 规则明确时推移片段。
- 所有操作在锁轨、未知媒体时长、越界 source range、相交片段与 stale revision 场景下都要有硬门和可读错误。范围不明就拒绝，不做静默近似。

### C. 把音量包络升级成通用关键帧系统

- 将“属性—关键帧列表—插值”作为统一模型，而不是给 opacity/scale/rotation/volume 各自发明字段。
- 关键帧时间使用 clip-relative 语义；trim/split 应裁切/重基准，而非把关键帧全局搬动。
- UI 至少支持添加、选择、移动、删除、数值编辑、播放头激活态；插值先覆盖 linear/ease-in/ease-out/ease-in-out，再评估 cubic Bezier 控制柄。
- 浏览器预览和 FFmpeg 输出必须共用一份曲线求值契约，用参数化测试验证；未支持的 easing/属性要明确拒绝或生成迁移 warning。

### D. 最后强化媒体生成与导出

- AI 先写结构、配旁白、确定节奏和镜头，再调用素材/生图/生视频工具；有成本的动作走应用侧授权，不把 secret 暴露给任意 prompt。
- 导出锁定 revision，并把输出文件、渲染 revision、QC、采样图和失败 clip 关联；不要把“命令成功”当成视觉验收通过。
- 增量渲染/镜头缓存放在正确性和一致性之后。当前渲染已有限制，不能因追求性能绕过帧回执与确定性检查。

## 每次改动的验收契约

- **Source**：`film.html` 外其他 clip/track/注释字节保持不变，除非操作要求更改。
- **Identity**：操作目标必须是现有 ID；不存在即失败，不按下标替代。
- **Revision**：stale AI 提案不得写盘；外部变化使待接受提案失效。
- **Gesture**：拖拽 preview 不写盘；释放后仅一事务/一次撤销。
- **Render**：先 validate，再 look，最后导出；校验失败要带具体 clip ID/资源 URI。
- **UI**：React 与 fallback 使用同一表单/operation contract；真实浏览器测试覆盖交互和文件落盘，不以静态字符串断言替代用户路径。
- **Honesty**：未实测支持的复杂网页效果、转场、曲线或媒体格式应显示为 unsupported/warning，不以“OpenFilm 兼容”或“剪映级”名义模糊带过。

## 本次直接落地

画布 Inspector 已补齐背景填色开关/背景色、不透明度、字重和文字对齐；背景透明语义被显式保留，额外属性只在确有变化时写入 overrides。React 实现、无构建时 fallback、浏览器回归断言与架构文档同步更新。后续能力按上述统一契约继续吸收，不再按功能名重复造轮子。
