# OpenFilm 吸收落地差距与 Video Director 目标架构

## 结论

**执行顺序已确定：先恢复真实 Studio 构建，再统一 React UI 所有权，然后收敛 AI 提案/人机编辑事务，最后进行真实浏览器与导出验收。当前操作直接提交到 main，不开新分支或 PR。**

目标不是把 OpenFilm 的代码逐文件复制进来，而是吸收它的**工程契约**：可读的影片文件、稳定 ID、文件系统作为 agent 与 Studio 的连接层、可验证的时间函数、按 ID 操作、确定性预览/导出。Video Director 已经有 FilmDocument、`film.html`、场景 `frame(t)` 契约、版本化操作、undo/redo、外部文件 watcher、AI 提案预览/确认和渲染验收，不能再另建一套平行影片模型。

最重要的剩余差距不是按钮数量，而是所有权边界：导演创作流程仍分布在 CompositionIR 工作台与 Film Studio 之间；FilmDocument 是最终时间线，但其编辑状态与剪辑数学仍通过原生 JS 服务桥驱动，React 已拥有单一根、轨道开关、时码尺 seek、片段指针生命周期与关键帧跳转，音量包络/波形/部分上下文菜单仍保留原生事件入口；frame(t) 的确定性范围小于任意网页；AI 支持 SSE 生命周期反馈、取消与转向后重规划，但不提供多人 CRDT 协作或同一模型会话中的 token 级 steering。

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
| React Studio | **单一 React root；AI 与 Inspector 并排；轨道锁/同步锁/隐藏/静音、时码尺 seek、片段选择/指针生命周期、关键帧跳转由 React 分发**；业务状态、source-aware 剪辑数学与部分 canvas/context-menu 交互仍走既有原生 JS/FilmDocument 桥。 | Studio 构建使用 self-hosted `[self-hosted, linux, x64]` runner 与独立 `studio_ui/` 包；React/Tailwind 构建、选定 Chromium 回归、JS 语法检查与产物发布均通过最新 CI，但不是全部剪辑组合或逐帧渲染一致性的证明 |
| 页面元素编辑 | 已支持 selector、文本、位置/尺寸、文字色、旋转；后端还支持背景色、不透明度、字重、对齐等受限字段 | Inspector 应暴露常用可编辑字段，并让 React 与无 bundle fallback 同契约；编辑不应意外物化未改字段 |
| 剪辑数学 | 已具备多个 source-aware 操作，但部分 roll/slide 条件严格；时间仍以秒浮点在许多接口流转 | 把帧吸附收敛到统一边界；长期评估整数 Tick/采样单位，而非全库冒险替换浮点 |
| 动画 | 音量包络可编辑；通用视觉关键帧可在 Inspector 编辑、在片段本体显示时间标记；split/trim/roll/slide 会按片段相对时间切片并保留 easing 曲线；自定义 Bezier 控制柄已通过保存/重载的真实 Chromium 回归（Studio #211 / b9b0adf4） | 完整预览与最终导出的逐帧一致性仍须在可运行的浏览器/渲染环境里验收；控制柄的交互与持久化测试不等于逐帧渲染一致性的证明 |
| 实时 AI | 已支持 SSE 生命周期事件、token 片段转发、停止当前请求、执行中转向后重新规划，以及基于 revision 的提案预览/拒绝/确认；正式文档仍只在确认后写入 | 假流单测覆盖 token delta/cancel；真实配置模型经部署代理的端到端 token 流尚未验证；继续补足目标定位、受影响片段、差异预览和验收结果。转向通过取消当前请求后以原指令+新指令重新规划实现，不是同一模型会话的 token 级 steering |
| 确定性与导出 | `frame(t)`、本地资源边界、look 重复抽样、Revision 绑定导出均已有；采样检查不是全帧证明 | 对每个场景验证资源 ready 和帧回执；导出错误定位到 clip ID；明确运行时支持边界，不宣称任意网页/任意 WebGL 都确定 |
| 产品入口 | 旧导演工作台与全屏 Film Studio 仍是两个工作上下文；初始化迁移后才由 `film.html` 接管 | 收敛为一个项目壳：导演规划、素材、场景、剪辑、AI 对话与导出围绕同一项目/同一最终时间线；迁移只是项目状态变化，不再像切换到另一款应用 |

## 当前下一阶段落地点

- React 对简单片段移动手势拥有预览位置状态；旧适配器负责从 FilmDocument 读取候选并计算 typed move resolution，React 负责预览显示，pointerup 仍通过 revision-checked FilmDocument 事务提交。修剪/roll/slide/slip 暂时保留各自原生预览入口。

## React 时间线输入所有权（当前增量）

- 片段右键菜单由 React 的单一 Studio root 渲染；复制、重复、粘贴、切分、删除、波纹删除和 Inspector 仍使用共享命令桥，原生 DOM 菜单仅留给无 bundle fallback。菜单开关不写入 FilmDocument revision。
- `#fs-timeline` 是 React Portal 宿主时，迁移页或文档错误页不能直接改写其子树。切换到 legacy 占位页前先卸载 React 时间线；恢复有效 FilmDocument 时，仅当 React surface 确认未挂载后，清理遗留占位内容并重新挂载，避免 React fiber 仍持有旧节点而浏览器 DOM 已被清空。

- 片段移动、修剪、roll、slide、slip 与音量包络的 pointer 生命周期由 React 分发；时间线几何、操作约束与吸附仍由共享适配器按当前 FilmDocument 计算，所有瞬态片段几何和 roll/slide/slip 提示由 React state 渲染。pointerup 仍只通过 revision-checked FilmDocument 事务提交；无 bundle fallback 保留原生预览入口。音量曲线的绘制和关键帧命中测试仍由共享适配器执行。
- 修剪起点/终点的原始目标、吸附候选、最小时长边界与最终目标由类型化 `resolveTrimStart/resolveTrimEnd` 同时驱动预览和 pointerup 提交，防止显示位置与落盘时间使用不同的限幅/吸附顺序。
- Roll/slide 的吸附锚点与可移动区间由类型化 `resolveRollGesture/resolveSlideGesture` 解析；预览和 pointerup 共用同一个 `deltaSeconds`。Roll 以右侧片段入点为锚点，Slide 以中间片段入点为锚点，并保留各自的相邻片段时长约束。
- 吸附线与跨轨拖放的目标/拒绝提示也作为 React 时间线瞬态状态渲染；原生适配器只计算目标 ID、吸附时间与可读提示。无 bundle fallback 才直接插入指示器/切换轨道 CSS 类，pointercancel 会清理吸附提示。
- 拖动中片段的虚线/淡化标记由同一个 React transient state 跟踪活跃 clip ID；原生 pointer adapter 不再改写 React clip 的 `classList`，no-bundle fallback 才会直接切换该类。
- 播放头位置由 React 订阅 FilmStore 的 `playhead` 字段并依据当前像素比例渲染；原生适配器保留运行时 seek/播放控制与 no-bundle fallback，不再直接写 React 时间线节点的 `style.left`。
- React 时间线内容宽度由 `fs-react-timeline-surface` 依据同一 `FilmTimelineSurfaceModel.totalWidth` 渲染；`#fs-timeline` Portal 宿主不再由原生适配器写 inline width，只有 no-bundle fallback 才直接设置宿主宽度。真实 Chromium Ctrl+滚轮回归校验 surface/scroll 宽度增大且宿主 inline width 不变。
- 这不是将所有剪辑算法迁入 React：修剪、roll、slide、slip 继续逐步迁移，最终写盘仍必须经过 revision-checked FilmDocument 事务。

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

- 本轮继续统一纯移动手势数学：类型化 `resolveMoveGesture` 同时负责零秒边界、阈值吸附与实际生效吸附目标，拖动预览和 pointerup 写入共用同一解析器。候选时间点仍由当前 FilmDocument 生成；FilmDocument 继续负责稳定 ID、轨道锁和 revision 校验。

- 以类型化纯函数统一时间换算、指针位移与自动滚动补偿、吸附目标排序和帧吸附；拖动过程中只画 preview/ghost，pointerup 才提交一次事务。吸附候选集仍由 FilmDocument 当前状态构造，类型化解析器只负责按屏幕像素阈值选择目标并稳定处理同距离候选。
- 已实现同项目多片段 Ctrl/⌘+C、Ctrl/⌘+V 和右键“重复”：保留片段相对布局与属性，在原轨道寻找可证明不重叠的区间，并作为一个 FilmDocument 事务提交；跨项目粘贴、覆盖式粘贴和拖入覆盖仍不支持。
- 时间线吸附现已覆盖片头、播放头与其他片段入/出点，按 8 个屏幕像素阈值计算；拖动期间显示吸附参考线，移动、修剪、滚动修剪和滑移式修剪的预览与提交都重新计算同一吸附目标。工具栏可关闭/启用吸附；右键菜单提供复制、重复、粘贴、切分、删除、波纹删除和打开属性检查器。
- Ctrl/⌘+滚轮以指针位置为锚点缩放、拖拽到时间线左右边缘时自动滚动，已通过构建后的 React Studio Chromium 回归验证：测试确认浏览器默认缩放被阻止、缩放后指针下的时间位置保持，以及右边缘拖动期间 scrollLeft 增加并将片段新入点写回 FilmDocument（Studio 工作流： https://github.com/just2512/video-director/actions/runs/39782935217）。跨轨多选拖动虽可形成同批移动操作，但对多轨同步关系的预览和失败恢复仍需补强。
- 清楚区分 Lift（留下空隙）与 Ripple Delete（关闭所有相关轨道上的空隙）；插入编辑仅在参与轨道/sync-lock 规则明确时推移片段。
- 所有操作在锁轨、未知媒体时长、越界 source range、相交片段与 stale revision 场景下都要有硬门和可读错误。范围不明就拒绝，不做静默近似。

### C. 通用关键帧系统已落地，剩余边界

- 通用视觉属性写入 `data-film-keyframes`，音频增益仍使用 `volume_envelope`，不混用两种语义。
- 关键帧时间是 clip-relative。split、trim、roll、slide 按新片段时长切片/重基准；切入/切出 eased segment 时用后端校验的 `window` 表达式保留插值形状。回归用例分别覆盖两个源时长的切分/修剪与 roll/slide。
- React Inspector 源码和可运行 fallback 都有曲线、节点拖动、帧步进时间/数值编辑、缓动、增加/删除/清空；时间线上显示关键帧标记，点击跳到影片绝对时间。fallback JSON 高级编辑仍共用同一 FilmDocument operation。
- 自定义 cubic Bezier 控制柄已在 React Inspector 实现（SVG 可拖控制柄并有 x1/y1/x2/y2 数值输入），关键帧 easing 支持 bezier 与切片保真的 window curve；仍需单独补真实浏览器保存/回读验收，并执行完整预览/导出逐帧一致性 Chromium + FFmpeg 验收。截至 2026-10-22，真实 Studio Chromium 回归覆盖时间线拖动/写回、轨道标志、关键帧跳转、pointercancel、Ctrl/⌘+滚轮锚点缩放和右边缘自动滚动；完整 AI Director 回归为 1256 passed、0 failed。该证据不覆盖自定义 Bezier 控制柄、全部专业剪辑操作排列或所有网页场景的逐帧确定性。

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

## 本轮落地记录

- 将 Studio UI 构建切换到仓库已有的 self-hosted WSL runner 标签（与 AI Director 工作流共用 runner 约定），不再请求 GitHub-hosted runner。
- 构建使用 Python 3.11 固定环境；Studio UI 有独立的 `studio_ui/` package，只安装 React、esbuild、TypeScript 与 Tailwind，不安装 Remotion renderer；渲染端依赖仍在 `remotion_runtime/` 单独安装。两条自托管工作流共用 `tools/bootstrap_python_socks.sh`，先修复 SOCKS5 下 pip 不可用的冷启动，再安装 Python/Playwright 依赖。首次 UI 构建生成 `studio_ui/package-lock.json`，后续走 `npm ci`，并将 lockfile 纳入与 bundle/CSS 相同的版本保护发布流程。
- 新增构建契约测试，要求工作流使用自托管 runner、保留依赖锁文件，且实际 JS/CSS 不能是占位输出。
- React Studio 的工具栏、时间线、素材列表、AI 面板、片段检查器和画布检查器改为通过一个 `createRoot()` 管理，并以 portals 挂载到既有布局槽位；这保留了现有 DOM 布局和事件桥接，但消除了多个 React root 独立管理同一个 Studio 的情况。
- 右侧属性检查器和 AI 导演不再通过 tab 互斥隐藏；二者常驻同一工作区，新增可用鼠标/上下方向键调整比例的分隔条，选择比例存入 `localStorage`。
- **当前验收状态（2026-10-22）：** 当前 `main` 的 React Studio bundle/CSS 已由自托管工作流构建并发布；定向 Studio 工作流和完整 AI Director 回归均通过（1256 passed、0 failed）。验证范围仍不包含所有媒体编解码组合、所有时间线操作排列、自定义 cubic Bezier 手柄与任意 HTML/WebGL 场景的逐帧确定性。

## 本次直接落地

画布 Inspector 已补齐背景填色开关/背景色、不透明度、字重和文字对齐；背景透明语义被显式保留，额外属性只在确有变化时写入 overrides。时间线已加入帧吸附开关/参考线、右键上下文菜单、重复片段和关键帧跳转；AI 规划已加入 SSE token 增量反馈、停止和“取消当前请求后按新要求重规划”。React 源码与无构建时 fallback 均有关键帧曲线编辑入口，且共享 FilmDocument 校验与写盘路径。  
**当前仍未关闭的验收门禁：** React 已有可运行构建产物并拥有单一根、scrubber 订阅、轨道控制、时码尺、片段手势分发与关键帧跳转；音量包络/波形和部分上下文菜单仍由原生事件处理。导演与 Studio 的产品入口尚未完全收敛；Bezier 控制柄已实现但需要真实浏览器保存/回读验收；多轨同步移动、Lift/Ripple/Delete/Insert 的完整组合仍需补充冲突和恢复测试；多种网页动画/异步资源仍不能宣称必然确定性。后续必须以具体用户路径的浏览器测试和实际 MP4/QC 证据关闭这些门禁，不能仅依赖静态源码测试。
