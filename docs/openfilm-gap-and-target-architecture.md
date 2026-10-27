# OpenFilm 吸收落地差距与 Video Director 目标架构

## 结论

**执行顺序已确定：先恢复真实 Studio 构建，再统一 React UI 所有权，然后收敛 AI 提案/人机编辑事务，最后进行真实浏览器与导出验收。当前操作直接提交到 main，不开新分支或 PR。**

目标不是把 OpenFilm 的代码逐文件复制进来，而是吸收它的**工程契约**：可读的影片文件、稳定 ID、文件系统作为 agent 与 Studio 的连接层、可验证的时间函数、按 ID 操作、确定性预览/导出。Video Director 已经有 FilmDocument、`film.html`、场景 `frame(t)` 契约、版本化操作、undo/redo、外部文件 watcher、AI 提案预览/确认和渲染验收，不能再另建一套平行影片模型。

最重要的剩余差距不是按钮数量，而是产品入口与外部 AI 服务的验收：导演创作流程仍分布在 CompositionIR 工作台与 Film Studio 之间；FilmDocument 是唯一最终时间线，React/FilmStore 已拥有多轨时间线显示、选择、波形/音量曲线、右键菜单、吸附/跨轨提示，以及 move/trim/roll/slide/slip 与音量包络的类型化 ID-only 手势上下文；React 路径的 `gestureDomRefs` 为空，DOM 引用只留给原生 fallback。共享适配器仍负责剪辑数学、FilmOperation 组合与 revision 检查；外部 revision 在包络手势期间到达时拒绝旧预览、保留外部文件并重载的真实 Chromium 回归已补齐。`frame(t)` 的确定性范围仍小于任意网页；AI 支持 SSE 生命周期反馈、取消与转向后重规划，但真实配置模型经中转端点的增量 token 传输仍须通过 opt-in 端到端烟测确认，且系统不提供多人 CRDT 协作或同一模型会话中的 token 级 steering。

## 目前已有的可复用基础

- **唯一最终时间线**：有效的 `film.html` 是最终编辑/渲染权威；初始化迁移是显式操作，不允许 CompositionIR 静默覆盖 FilmDocument。
- 项目选择器的 `change` 事件只通过 `editorLoad()` 进入项目工作流；该包装层负责决定打开或重载嵌入的 Studio，不能再注册一条并发 `filmStudioLoad` 旁路。真实 Chromium 回归要求 Studio 已打开时再次切换/打开项目只执行一次 FilmDocument reload，防止旧请求覆盖较新项目状态。
- `editorLoad()` 在调用旧 CompositionIR loader 之前先查询 FilmDocument 权威状态；只要 `film.html` 存在（包括无法解析的情况），就直接进入 Studio 并由 Studio 显示有效/损坏状态。只有服务器明确确认 `film.html` 不存在时才调用旧工作台；状态查询失败时 fail-closed，不猜测项目仍可按旧时间线编辑。
- 项目选择器不再另装第二条自动打开 Studio 的 `change` 监听。直接下拉操作只走 `editorLoad()` authority-first 包装器；`workspaceOpen()` 则显式调用 FilmDocument 打开入口。真实 Chromium 首次打开与再次切换均断言只执行一次状态检查和一次 Studio 加载，且完全不读取旧 CompositionIR。
- **文件化协作**：项目文件、agent CLI 与 Studio watcher 连接外部编辑；稳定快照经解析后发 revision 事件，坏文档会冻结编辑而不是被 Studio 覆盖。
- **冲突保护**：编辑操作绑定 revision 和 clip ID；旧 revision 的 AI 提案拒绝应用；缺失 ID 不会改投另一个片段。人机共享的是文本文件与可审计操作，不是聊天上下文中的隐藏状态。
- **编辑/渲染闭环**：多轨、轨道锁定/隐藏/静音、剪切、修剪、插入编辑、ripple delete、slip/roll/slide（有意限制适用条件）、愈合切口、淡入淡出、音量包络、波形、缩略图、撤销/重做和基于真实浏览器的 look/render 均已有实现。
- **任何编程智能体的入口**：`docs/FILM_AGENT_MANUAL.md` 和 `python -m video_director film ...` 已提供通用工作约定；不需要把 Codex、Claude Code 或其他 agent 的进程硬塞进 Studio。agent 可在项目工作目录读写场景/影片文件并使用相同 CLI 校验。

## 与 OpenFilm 指纹级对照的真实差距

| 维度 | 当前状态 | 必须补齐的语义 |
|---|---|---|
| 影片文档 | **已具备主要骨架**；HTML/轨道/片段/ID 与可编辑 overrides 已落地 | 固化版本化格式契约与迁移门禁；未知能力明确 warning/reject，不能“看似转成功” |
| 文件协作 | **已部分实现**：稳定快照 watcher、SSE、revision 检查、外部修改历史 | 明确区分 Studio 操作、CLI 操作、原始文件改写；在 UI 显示外部变更和操作来源；不宣称多人并发 CRDT |
| React Studio | **单一 React root；AI 与 Inspector 并排；轨道开关、时码尺 seek、片段/音量包络 pointer 生命周期、波形/包络绘制、右键菜单、剪辑瞬态几何与关键帧跳转由 React/FilmStore 拥有**；操作上下文现在是 FilmStore 的 ID-only gesture context，剪辑数学与 revision-checked FilmDocument 写入仍经共享适配器。 | Studio 构建使用 self-hosted `[self-hosted, linux, x64]` runner 与独立 `studio_ui/` 包；React/Tailwind 构建、选定 Chromium 回归、JS 语法检查与产物发布均通过最新 CI，但不是全部剪辑组合或逐帧渲染一致性的证明 |
| 页面元素编辑 | selector、文本、位置/尺寸、文字/背景色、不透明度、字重、对齐、字号、旋转、行高与字间距均可编辑；React 与无 bundle fallback 共用字段契约 | 继续覆盖剩余受限属性；Inspector 只提交实际变化字段，不物化未改文本/位置/样式；字段校验保持与服务端 allowlist 同步 |
| 剪辑数学 | 已具备多个 source-aware 操作，但部分 roll/slide 条件严格；时间仍以秒浮点在许多接口流转 | 把帧吸附收敛到统一边界；长期评估整数 Tick/采样单位，而非全库冒险替换浮点 |
| 动画 | 音量包络可编辑；通用视觉关键帧可在 Inspector 编辑、在片段本体显示时间标记；split/trim/roll/slide 会按片段相对时间切片并保留 easing 曲线；自定义 Bezier 控制柄已通过保存/重载的真实 Chromium 回归（Studio #211 / b9b0adf4） | 完整预览与最终导出的逐帧一致性仍须在可运行的浏览器/渲染环境里验收；控制柄的交互与持久化测试不等于逐帧渲染一致性的证明 |
| 实时 AI | 已支持 SSE 生命周期事件、token 片段转发、停止当前请求、执行中转向后重新规划，以及基于 revision 的提案预览/拒绝/确认；正式文档仍只在确认后写入 | 假流单测覆盖 token delta/cancel；新增 `tests/test_film_studio_live_ai_streaming.py` 的显式 opt-in 烟测，可对当前 `VD_LLM_API_KEY` / `VD_LLM_BASE_URL` / `VD_LLM_MODEL` 配置发起真实请求，验证多个 token 事件在 result 前到达且 `film.html` 未写入。默认 CI 跳过该测试，因此在运维者实际启用并通过前，实时端到端仍标记 UNVERIFIED。转向仍是取消当前请求后用原指令+新指令重新规划，不是同一模型会话的 token 级 steering |
| 确定性与导出 | `frame(t)`、本地资源边界、look 重复抽样、Revision 绑定导出均已有；采样检查不是全帧证明 | 对每个场景验证资源 ready 和帧回执；导出错误定位到 clip ID；明确运行时支持边界，不宣称任意网页/任意 WebGL 都确定 |
| 产品入口 | 全屏 Studio 顶栏现在复用同一个项目选择器，支持不退出时间线切换项目；关闭时原位还原，仍经过 `editorLoad()` 的文档权威、revision、拖动和未提交草稿保护。不同项目状态的导演规划与剪辑仍存在迁移阶段差异 | 继续把导演规划、素材、场景、剪辑、AI 对话和导出收敛到同一项目壳/同一最终时间线；不能把项目切换器进入 Studio 误认成所有入口已统一 |

## 真实模型 Token 流验收的执行方式

- 真实模型测试不是普通 SSE mock 测试的别名。只有 `tests/test_film_studio_live_ai_streaming.py` 收到至少两个真实 `token` 事件，并确认这些事件在 `result` 事件之前到达、生成提案前后 `film.html` 字节与 revision 不变，才可记录为 PASS。
- 自托管 Runner 需要配置 `VD_LLM_API_KEY`、`VD_LLM_BASE_URL`、`VD_LLM_MODEL`。可在 GitHub Actions 手动运行 **AI Director regression** 并将 `run_live_llm=true`；或在 main 推送提交信息包含 `[live-stream-smoke]` 的提交。这个请求可能产生模型服务费用。
- 每次普通回归会在 Actions Summary 显示 `PASS / MISSING / REJECT / UNKNOWN`：测试被跳过只能是 MISSING，不能以 mock 通过替代真实服务证据。MISSING 不意味着应用构建失败，但意味着“真实模型逐段 Token 流”验收仍未关闭。
- 默认 push/PR 回归不主动调用真实模型；未显式 opt-in 时应标记为 MISSING，不阻断无模型配置下的基础构建和浏览器回归。显式 opt-in 后，若缺少凭据或真实模型烟测失败，则必须 REJECT 并阻断发布。当前自托管 Runner 未配置真实模型凭据，真实 token 流烟测由用户拉取后在本地执行。

## 当前下一阶段落地点

- 普通片段 move/trim/roll/slide/slip 的活动手势上下文已由 FilmStore 的 `timelineGestureContext` 类型化保存：仅含指针 ID、模式、稳定 clip/track ID、原始时间/时长、目标轨道状态及成员数值快照；Store 对必要字段与成员身份做严格校验。React 路径不保存 DOM 引用。
- 音量包络活动手势已迁为 FilmStore 的 `timelineEnvelopeGesture`：只保存 clip ID、pointer ID、数值矩形、持续时间、关键帧快照、索引与活动标志；Canvas 引用只保留在无 bundle fallback 的适配器闭包里。真实 Chromium 已验证 ID-only Store 快照、React 预览、pointercancel 清理、pointerup 单事务和 undo。
- Agent/外部进程在音量包络指针手势期间写入新 revision 的冲突边界已由真实 Chromium 回归覆盖：外部文件 revision 被保留；旧预览在 pointerup 时被丢弃，不会发送 edit 请求；FilmStore 活动手势/预览清空，随后重载到外部 revision。该测试随 AI Director #1349 和 Studio #340 通过。

## React 时间线输入所有权（当前增量）

- Studio 打开时先保持 CompositionIR 控制入口关闭；服务端确认项目没有 `film.html` 后才将旧导演控制网格和命令栏挂入迁移/初始规划面板。一旦 `film.html` 存在（有效或损坏），这些旧写入入口返回隐藏的兼容编辑器，导演页只保留工作流状态，并明确提示正式修改走 FilmDocument revision 校验的右侧 AI 提案。初始化成功后立即关闭旧入口；切换回无影片文档项目时再恢复。
- Studio 项目切换已使用单个 DOM 选择器节点：全屏时移到顶栏，退出时还原；浏览器回归覆盖跨项目切换及未提交 Inspector 草稿时的拒绝路径。切换仍走原有 authority/revision 门禁，不再复制一个 selector，也不引入新的项目状态源。

## Inspector 字段级编辑语义

- 画布元素 Inspector 对几何、文字、颜色、字重、对齐、透明度、字号、旋转、行高和字间距只生成差异字段；未修改的文本/样式不进入 `element_set`。若没有差异，不创建 revision。
- `lineHeight` 与 `letterSpacing` 输入遵循 FilmDocument 的安全 CSS 长度格式；空值表示不修改当前值。前端错误不提交，服务端继续执行最终 allowlist/长度/范围校验。

## AI 提案 revision 可见门禁

- React 提案面板同时接收当前 `FilmStore` revision 与提案 `baseRevision`；二者不一致时显示过期提示并禁用预览/应用，保留拒绝操作。服务端 `apply-proposal` revision gate 仍是最终安全边界，前端禁用按钮不替代服务端校验。

- 片段右键菜单由 React 的单一 Studio root 渲染；复制、重复、粘贴、切分、删除、波纹删除和 Inspector 仍使用共享命令桥，原生 DOM 菜单仅留给无 bundle fallback。菜单开关不写入 FilmDocument revision。
- FilmDocument 历史在 operation payload 之外记录顶层 `source`（Studio、素材插入、AI 提案、CLI 或外部文件）。Studio 在成功载入 watcher 检测到的外部 revision 后显示来源标签，避免用户只知道 revision 改变却不知道是谁写入。
- `#fs-timeline` 是 React Portal 宿主时，迁移页或文档错误页不能直接改写其子树。切换到 legacy 占位页前先卸载 React 时间线；恢复有效 FilmDocument 时，仅当 React surface 确认未挂载后，清理遗留占位内容并重新挂载，避免 React fiber 仍持有旧节点而浏览器 DOM 已被清空。

- 真实 Chromium 回归还覆盖包络手势的 pointercancel：Store 中的活动上下文只允许 clip/pointer ID、数值矩形、关键帧数值快照和状态字段；取消后上下文/预览都必须清空，FilmDocument revision 和 edit 请求数不变。
- 音量包络 pointerdown 的命中测试、插入/删除判定、关键帧时值/增益约束，以及 pointerup 的三位小数归一化与重复时间合并由 `film-timeline-math.ts` 纯函数承担。React 路径向适配器只传 clip ID 与每帧数值矩形，不保留 Canvas 引用；无 bundle fallback 保留原生计算路径。
- 时间线手势预览、吸附线、轨道 drop-target、活动拖动 ID、音量包络 preview 与波形峰值源数据作为 FilmStore 的类型化瞬态字段。React TimelineSurface 负责音量包络与音频波形画布绘制；正常 React 路径向包络手势适配器传片段 ID 与每帧矩形数值，不传 canvas/HTMLElement。旧适配器保留受大小/时长限制的异步音频解码缓存，并只将峰值数组发布到 Store。手势适配器仍负责关键帧命中/移动约束，并在 pointerup 经 revision-checked FilmDocument 事务提交；无 bundle fallback 保留原生绘制入口。
- 修剪起点/终点的原始目标、吸附候选、最小时长边界与最终目标由类型化 `resolveTrimStart/resolveTrimEnd` 同时驱动预览和 pointerup 提交，防止显示位置与落盘时间使用不同的限幅/吸附顺序。
- Roll/slide 的吸附锚点与可移动区间由类型化 `resolveRollGesture/resolveSlideGesture` 解析；预览和 pointerup 共用同一个 `deltaSeconds`。Roll 以右侧片段入点为锚点，Slide 以中间片段入点为锚点，并保留各自的相邻片段时长约束。
- 没有显式吸附候选落入屏幕像素阈值时，trim-start、trim-end、roll、slide 与 move 使用同一影片帧率网格兜底；显式片段边缘/播放头候选优先于帧网格。 帧候选命中或显式吸附目标本身已在帧网格时，pointerup 以 6 位小数写入解析后的目标/增量，避免统一三位小数截断把已对齐的值重新移出帧网格；非帧对齐的自由编辑仍保持三位小数写入。
- Slip 的时间线拖动量到源素材偏移由纯函数 `resolveSlipGesture` 按播放速率统一换算，预览标签与最终 `slip.delta_source` 共用这一结果；不改变片段时间线起点/时长。
- 吸附线与跨轨拖放的目标/拒绝提示也作为 React 时间线瞬态状态渲染；原生适配器只计算目标 ID、吸附时间与可读提示。无 bundle fallback 才直接插入指示器/切换轨道 CSS 类，pointercancel 会清理吸附提示。 鼠标离开所有轨道行时清空旧目标并设置显式拒绝原因，不能继承上一次悬停轨道作为提交目标。
- 拖动中片段的虚线/淡化标记由同一个 React transient state 跟踪活跃 clip ID；原生 pointer adapter 不再改写 React clip 的 `classList`，no-bundle fallback 才会直接切换该类。
- 关键帧标记的 pointer/click 在 React 时间线中由按钮自身消费，跳转只调用共享 seek bridge；无 React bundle 时才安装原生 click 委派，不再在 Portal 宿主捕获阶段拦截 React 指针事件。
- 片段选中态由 React 从 FilmStore 的 selectedIds 投影；原生适配器只更新选中 ID，不再在 React 路径中改写 clip classList。fallback 仍用原生类切换。
- React 时间线工具栏的插入/覆盖/吸附/音量曲线/slide/slip 按钮，以及时间线 surface 的 envelope/slide/slip 模式类都订阅 FilmStore。原生适配器只切换模式字段；外层 panel 的模式 class、按钮文字/pressed 属性和 active class 仅由 no-bundle fallback 更新。
- MP4 导出按钮的禁用态同样由 React 订阅 `FilmStore.rendering`; 原生导出流程只更新导出任务状态，不直接写按钮 `disabled`。导出依旧绑定启动时的 FilmDocument revision，任务终态统一恢复按钮状态，fallback 保留既有行为。
- 播放/暂停标签和 `aria-pressed` 由 React ScrubRow 订阅 `FilmStore.playing`；runtime 的时间/暂停消息和用户播放操作更新同一状态。原生按钮文字更新只留给 no-bundle fallback，播放/定位不创建 FilmDocument revision。
- 当完整 React 时间线拥有手势预览和活动 clip ID 时，多选手势上下文只保存稳定 ID、原始时间/边界和轨道索引，不再逐片段查询或保留 DOM 节点。roll/slide 的邻接 DOM 引用仅在原生 fallback 中收集。
- 播放头位置由 React 订阅 FilmStore 的 `playhead` 字段并依据当前像素比例渲染；原生适配器保留运行时 seek/播放控制与 no-bundle fallback，不再直接写 React 时间线节点的 `style.left`。
- React 时码尺提供 slider 无障碍语义、点击刻度、按下拖动连续 seek，以及方向键按 1 秒/Shift+方向键按 0.1 秒定位；React 处理方向键后停止向 document 级 NLE 快捷键冒泡，避免同一次按键同时执行帧级 Nudge。所有输入仍通过同一 seek bridge 更新 FilmStore、播放器与 monitor 时间，不生成 FilmDocument 事务。
- 外部 revision 改变时保留旧 AI 提案用于查看差异/拒绝，并记录观测到的新 revision；React 面板显式标记过期并禁用预览/应用，命令桥也会拒绝对旧提案的直接调用。服务器 revision gate 仍是最终安全边界。
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
- 普通删除在界面上明确命名为“删除并保留空隙”，波纹删除明确命名为“收拢时间线”；真实 Chromium 回归验证普通删除不移动后续片段，波纹删除跨轨收拢、切分跨越区间的素材，并通过 undo 恢复操作前的精确 film.html 字节。
- 已实现同项目多片段 Ctrl/⌘+C、Ctrl/⌘+V 和右键“重复”：保留片段相对布局与属性，在原轨道寻找可证明不重叠的区间，并作为一个 FilmDocument 事务提交；跨项目粘贴、覆盖式粘贴和拖入覆盖仍不支持。
- 时间线吸附覆盖片头、播放头与其他片段入/出点，按 8 个屏幕像素阈值计算；若阈值内没有明确对齐目标，则按当前 FilmDocument fps 回退到最近帧。明确的片段/播放头/片头候选优先于帧回退；关闭吸附会同时关闭帧对齐。移动、修剪、roll、slide 与 slip 的预览和提交统一使用当前帧率：slip 将源区间位移按播放速度换算后帧对齐。拖动预览使用同一解析结果，pointerup 仍经 FilmDocument revision 校验。右键菜单提供复制、重复、粘贴、切分、删除、波纹删除和打开属性检查器。
- Ctrl/⌘+滚轮以指针位置为锚点缩放、拖拽到时间线左右边缘时自动滚动，已通过构建后的 React Studio Chromium 回归验证：测试确认浏览器默认缩放被阻止、缩放后指针下的时间位置保持，以及右边缘拖动期间 scrollLeft 增加并将片段新入点写回 FilmDocument（Studio 工作流： https://github.com/just2512/video-director/actions/runs/39782935217）。跨轨多选移动会在每个合法目标轨道显示 React 管理的非交互 ghost，原片段在提交前保持淡化；非法目标不显示 ghost。释放后所有成员仍作为一个 FilmDocument 批量事务提交，拒绝/取消需清理 Store 瞬态状态并保留原始轨道与 revision。
- 清楚区分 Lift（留下空隙）与 Ripple Delete（关闭所有相关轨道上的空隙）；插入编辑仅在参与轨道/sync-lock 规则明确时推移片段。
- 所有操作在锁轨、未知媒体时长、越界 source range、相交片段与 stale revision 场景下都要有硬门和可读错误。范围不明就拒绝，不做静默近似。

### C. 通用关键帧系统已落地，剩余边界

- 通用视觉属性写入 `data-film-keyframes`，音频增益仍使用 `volume_envelope`，不混用两种语义。
- 关键帧时间是 clip-relative。split、trim、roll、slide 按新片段时长切片/重基准；切入/切出 eased segment 时用后端校验的 `window` 表达式保留插值形状。回归用例分别覆盖两个源时长的切分/修剪与 roll/slide。
- React Inspector 源码和可运行 fallback 都有曲线、节点拖动、帧步进时间/数值编辑、缓动、增加/删除/清空；时间线上显示关键帧标记，点击跳到影片绝对时间。fallback JSON 高级编辑仍共用同一 FilmDocument operation。
- 自定义 cubic Bezier 控制柄已在 React Inspector 实现（SVG 可拖控制柄并有 x1/y1/x2/y2 数值输入），并已通过真实 Chromium 修改、应用 FilmDocument、刷新页面后回读持久化值的验收（`tests/test_film_studio_canvas_history_browser.py::test_react_bezier_control_drag_persists_after_browser_reload`）。本轮新增另一条真实导出验收：无蒙版蓝色层使用非线性 cubic-bezier，分别检查浏览器 look 在 0/0.25/0.5/0.75s 的绝对像素中心确实遵循曲线，并将相同时间戳从 FFmpeg 编码帧回读，比较蓝色层位置。蒙版/相机动态的红色层继续保留独立测试，避免其裁剪影响 Bezier 几何判定。截至 2026-10-22，真实 Studio Chromium 回归覆盖时间线拖动/写回、轨道标志、关键帧跳转、pointercancel、Ctrl/⌘+滚轮锚点缩放和右边缘自动滚动；完整 AI Director 回归为 1256 passed、0 failed。该证据不覆盖自定义 Bezier 控制柄、全部专业剪辑操作排列或所有网页场景的逐帧确定性。

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
- **Honesty**：当前时间线右键菜单提供同轨、无间隙相邻片段的“淡出并淡入下一片段”操作，沿用片段 fade 属性、不移动片段位置；它是淡黑衔接，不是重叠式 crossfade。未实测支持的复杂网页效果、重叠式转场、曲线或媒体格式应显示为 unsupported/warning，不以“OpenFilm 兼容”或“剪映级”名义模糊带过。

## 本轮落地记录

- 将 Studio UI 构建切换到仓库已有的 self-hosted WSL runner 标签（与 AI Director 工作流共用 runner 约定），不再请求 GitHub-hosted runner。
- 构建使用 Python 3.11 固定环境；Studio UI 有独立的 `studio_ui/` package，只安装 React、esbuild、TypeScript 与 Tailwind，不安装 Remotion renderer；渲染端依赖仍在 `remotion_runtime/` 单独安装。两条自托管工作流共用 `tools/bootstrap_python_socks.sh`，先修复 SOCKS5 下 pip 不可用的冷启动，再安装 Python/Playwright 依赖。首次 UI 构建生成 `studio_ui/package-lock.json`，后续走 `npm ci`，并将 lockfile 纳入与 bundle/CSS 相同的版本保护发布流程。
- 新增构建契约测试，要求工作流使用自托管 runner、保留依赖锁文件，且实际 JS/CSS 不能是占位输出。
- React Studio 的工具栏、时间线、素材列表、AI 面板、片段检查器和画布检查器改为通过一个 `createRoot()` 管理，并以 portals 挂载到既有布局槽位；这保留了现有 DOM 布局和事件桥接，但消除了多个 React root 独立管理同一个 Studio 的情况。
- 右侧属性检查器和 AI 导演不再通过 tab 互斥隐藏；二者常驻同一工作区，新增可用鼠标/上下方向键调整比例的分隔条，选择比例存入 `localStorage`。
- **当前验收状态（2026-10-22）：** 当前 `main` 的 React Studio bundle/CSS 已由自托管工作流构建并发布；定向 Studio 工作流和完整 AI Director 回归均通过（1256 passed、0 failed）。验证范围仍不包含所有媒体编解码组合、所有时间线操作排列、自定义 cubic Bezier 手柄与任意 HTML/WebGL 场景的逐帧确定性。

## 本次直接落地

画布 Inspector 已补齐背景填色开关/背景色、不透明度、字重和文字对齐；背景透明语义被显式保留，额外属性只在确有变化时写入 overrides。时间线已加入帧吸附开关/参考线、右键上下文菜单、重复片段和关键帧跳转；AI 规划已加入 SSE token 增量反馈、停止和“取消当前请求后按新要求重规划”。React 源码与无构建时 fallback 均有关键帧曲线编辑入口，且共享 FilmDocument 校验与写盘路径。  
**已通过的时间线验收（2026-10-25）：** React/FilmStore 已拥有时间线视图、选择、拖动/包络预览、吸附/跨轨目标、波形来源与右键菜单的状态和呈现；共享旧适配器继续负责来源感知的剪辑数学、FilmOperation 组合与 revision 校验写盘，不再作为这些 React 视图状态的第二份所有者。

- [Studio #425](https://github.com/just2512/video-director/actions/runs/40186157116) 的真实 Chromium 回归验证：素材覆盖等待解析时若外部进程写入新 revision，服务端返回 REVISION_CONFLICT，外部文件字节保留，过期事务不会覆盖新内容。
- [Studio #426](https://github.com/just2512/video-director/actions/runs/40186377769) 验证同步锁多轨插入在一次 Undo 后恢复原始 film.html 字节。
- [Studio #427](https://github.com/just2512/video-director/actions/runs/40186926343) 验证成功覆盖在一次 Undo 后恢复原始 film.html 字节，并且此前的 stale/busy/submitter-failure 清理探针不会污染编辑历史。

**仍未关闭的验收门禁：** 现有 Chromium 回归覆盖基础跨轨移动、Lift/Ripple/Delete/Insert/Overwrite、速度预设、roll/slide/slip、撤销重做和外部 revision 冲突，但并未穷尽所有操作顺序。`test_react_clip_drag_is_discarded_when_revision_changes_mid_gesture` 已在 Studio 浏览器回归中通过，覆盖拖动期间外部 revision 变化时拒绝旧手势；延迟返回的过期 AI 提案回归也已验证禁止预览/应用。淡出/淡入菜单已加入成功和拒绝路径的真实 Chromium 测试；`test_adjacent_clip_fades_match_browser_look_and_exported_mp4_frames` 新增真实 MP4 帧与 Look 预览的像素一致性检查，须等待最新主线 AI Director 回归确认。Bezier/HTML 场景及任意网页动画、异步资源的确定性边界不能泛化；真实配置模型经中转端点的增量 token 传输仍由用户本地执行 opt-in 端到端烟测，CI 当前是 `MISSING`，不视为 PASS。必须继续用具体用户路径的浏览器测试和实际 MP4/QC 证据关闭这些门禁，不能仅依赖静态源码测试。


- 时间尺 click 与 scrub 共用 React 指针生命周期：父时间尺统一捕获指针，刻度点击在 pointerup 按 `data-at` 精确定位，真实移动超过 4px 才启动 scrub；拖动结束不会被子刻度的合成 click 拉回起点。
