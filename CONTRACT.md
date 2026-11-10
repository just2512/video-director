# Creative OS 全局开发契约（CONTRACT）

> 本文件是项目级最高工程契约，改编自《全局开发契约与集成验收闸门.md》并适配本仓库实际栈。
> 所有 Backend / Frontend / AI / Renderer / Registry / Workflow Agent 均必须遵守（包括 Claude Code 本身）。
> 本文件定义"系统必须如何工作"，不是具体 Task 的实现方案。
> 修改本文件 = 显式 Contract Change，须先做影响分析。

---

## 0. 本仓库栈适配（原文通用条款 → 本仓库执行口径）

| 原文条款 | 本仓库执行口径 |
|---|---|
| 后端 `composer/` 目录结构 | `video_director/` 包（registries/ 子包已按方案落位） |
| 前端 UI 与构建 | Studio UI 使用 React 19 + Tailwind 4；构建、浏览器回归与发布验证仅使用 GitHub-hosted Actions runner（Linux `ubuntu-24.04`，Windows 原生测试 `windows-latest`）。`webui/server.py` 仍使用 Python 标准库提供本地服务。禁止维护第二套可见编辑 UI 或第二份权威影片状态。 |
| `npm run typecheck / test / build` | `python -m pytest tests/ -q`（单测/契约）+ `python tools/test_webui_*.py`（Playwright DOM 断言 E2E） |
| 验收证据 "screenshot" | **禁止截图**（Jerry 全局规则）。UI Gate 证据 = Playwright DOM 断言输出（断言文本 + 元素存在性） |
| frontend/src/api/client.ts | WebUI 内 `fetch()` 统一走 `webui/server.py` 端点；前端不直接拼第三方 URL |


### 0.1 Studio 前端契约变更（2026-10-22）

原适配表中的“禁止引入 React/Vite/npm 栈”与已经进入仓库的 React 19 Studio、Tailwind 4 源码和构建流程冲突。该限制现明确变更为：

- React 19 是 Studio 正常运行时的可见 UI 所有者；Tailwind 4 只在构建时生成 CSS，不要求终端用户安装 Node.js。静态 fallback 仅用于构建/运行故障时恢复同一 FilmDocument 服务，不具备独立影片状态或写盘通道，正常 React 构建运行时必须隐藏。
- Python 标准库 WebUI 服务与现有 FilmDocument/渲染能力保留；React 不建立第二份影片状态、剪辑时间线或独立写盘路径。
- Studio UI 构建、浏览器回归、媒体/导出验收和发布验证统一使用 GitHub-hosted Actions runner：Linux 使用固定镜像 `ubuntu-24.04`，Windows 原生文件替换/进程锁测试使用 `windows-latest`。禁止使用 `self-hosted`、本地 WSL、开发者工作区或本机模型配置作为仓库 CI 执行环境。
- 构建依赖通过 `package-lock.json` 固定；只有 typecheck、bundle/CSS 非占位检查和指定浏览器测试通过，构建产物才允许由工作流发布回 `main`。
- 原生 `film-studio.js` 在迁移阶段仅作为编辑服务/运行时桥接与兼容路径；后续工作必须持续收敛 UI 与手势所有权，不能以“React 已有组件”为由宣称完成接管。

该变更影响前端构建、Studio DOM 所有权、React 与 fallback 的交互契约及 CI：由本次工作流测试新增 `tests/test_studio_build_contract.py` 固定 runner、锁文件和真实构建产物约束。未由实际 runner 验收的能力仍标记为待验证。


### 0.2 CI Runner 契约统一（2026-11-10）

- 本节与 §0 栈适配表共同构成唯一 Runner 规则：所有仓库 CI、构建、浏览器/媒体回归、Windows 原生验证均运行于 GitHub-hosted Actions runner。
- Linux/browser/media/build jobs 固定使用 `ubuntu-24.04`；Windows 原生文件持久化与进程锁验证使用 `windows-latest`。具体 job 可按测试所需拆分，但不得更换为自托管或本地执行环境。
- 禁止 `self-hosted` runner、WSL runner、开发者工作区、本机凭据/模型配置进入仓库 CI。工作流不得依赖已删除或已废弃的 `ai-director.yml`。
- 正常 push 回归与真实 NVIDIA provider acceptance 分离。真实 provider 调用仅允许在独立 GitHub-hosted job 中，由显式 `workflow_dispatch` opt-in 控制，并仅从 GitHub Actions Secret 读取凭据；不得因普通 push 自动调用或读取本地配置。
- 此规则与 `docs/parallel-workstreams.md` 的 Runner policy、现行工作流及 `tests/test_studio_build_contract.py` 的硬性断言保持一致。修改 Runner 类别必须按 Contract Change 重新进行影响分析并更新测试与执行文档。

其余条款按原文执行，逐条如下。

---

## 1. 核心生产契约

```text
Understand → Propose → Confirm → Compose → Generate → Preview
→ Revise → Recompose → Render → Verify → Distribute → Remember
```

任何新 Capability 不得绕过核心生产链建立自己的私有生产流程。

## 2. 核心对象分层

```text
Source → Asset → Creative Plan → Recipe/Motion/Template
→ Composition → Timeline → Artifact → Render → QC
```

不得混淆：
```text
Template != MP4        Recipe != Timeline       Motion != Renderer
Asset != Artifact       Draft != Final          Proposal != Applied Change
Reference != Source Asset   Preview != Final Render
```

## 3. Source 不可变

原始视频/音频/图片/PDF/字幕/录音/录屏/用户文档一经导入，不得被 AI、Editor、Renderer、Revision 原地修改。允许 extract/transcode/analyze/derive/crop/segment，但派生必须形成新 Artifact 并保留 `sourceArtifactIds`。

## 4. AI 行为契约

AI 可以：Understand / Analyze / Infer / Propose / Resolve / Explain / 生成结构化 plan / 生成结构化 patch。

AI 不可以：修改 Source、覆盖正式 Composition、执行任意 shell、写任意 FFmpeg 命令、绕过 Validation、绕过 Human Confirmation、伪造 Evidence/Provenance、声明未检查过的画面事实。

AI 输出必须是结构化对象。禁止 `AI → arbitrary JSON mutation`；必须 `AI → Structured Proposal → Validation → Command/Patch → Preview → Confirm → Apply`。

## 5. Proposal != Apply

所有 AI 建议必须区分 `PROPOSED / PREVIEWED / APPLIED / REJECTED`。

**默认 PROPOSED ≠ APPLIED。** 用户点击 Reject 后：Composition 不变、Artifact 不变、Revision 不增加、Source 不变。

## 6. 项目活动状态的唯一权威事实源

Editor、AI、Template、Recipe、Generation 都不得直接修改最终 Render 文件。一个项目在任一时刻只能有一份权威的物理剪辑状态，不允许 CompositionIR 与 film.html 双写或靠后台同步任务猜测谁是最新值。

- **尚未迁移的旧项目**：CompositionIR 仍是其兼容编辑与渲染事实源。
- **已初始化/迁移出 film.html 的项目**：film.html 是唯一权威的物理影片文档；所有人工编辑、AI 编辑、生成结果入轨、撤销重做和正式导出必须通过 FilmDocument 操作服务读取、验证和提交同一 revision。
- **创作计划、Shotbook、Storyboard 和 WorkflowRun**：保留脚本、镜头意图、素材来源、任务状态等语义信息，但不得另建一份能独立覆盖活动项目物理时间线的状态。
- **迁移行为**：必须显式预览警告与用户确认；转换未覆盖的字段不得静默丢弃。film.html 存在时，旧 CompositionIR 写入和导出必须 fail closed，不得回退到旧状态。
- **修改活动项目事实源契约**：先进行跨模块影响分析并更新本节与相关测试，再开始实现；不得只改单个调用点。

统一生产路径：`Intent/Plan → Validated Film Operation → FilmDocument Revision → Dependency/QC → Revision-pinned Render → Verify`。旧项目在明确迁移前继续沿用旧路径，迁移后不得混用两条写入路径。

## 7. Timeline 契约

语义时间轴（Narration/Beat/Scene/Intent）与物理时间轴（start/duration/frame/track/layer/in/out/speed）两层并存。旁白通常是语义主轴，但 narration-first / dialogue-first / music-first / image-only / video-only / mixed-media / silent 全部必须合法。

**Narration Timeline 铁律（本仓库已实现 QC 强制）**：转场与动画不得移动旁白和字幕时间；总时长必须等于旁白时长（`qc duration_vs_narration` 检查）。

## 8. Layer 契约

Layer 必须至少支持：video/image/person/text/subtitle/title/lower_third/graphic/shape/svg/icon/chart/background/logo/lottie/pip。Layer 不得直接依赖具体 Renderer。

**本仓库现状**：schema 全量支持（models.LayerType），第一阶段渲染器仅实现 video/image（validator 对其余类型诚实 REJECT "LAYER_NOT_RENDERABLE"，不静默忽略）。

## 9. Motion 契约

Motion 是 Renderer-independent 的声明。Registry 中允许 property/keyframe/duration/easing/parameter/semantic_intent/constraints；**禁止 arbitrary shell / arbitrary FFmpeg / arbitrary executable JavaScript / Remotion 组件**（core.validate_item 的 FORBIDDEN_FIELDS 显式拒收）。

Motion 必须可以 Preview / Instantiate / Persist / Reopen / Render。

## 10. Recipe 契约

Recipe 声明 semantic intent / content type / duration / slots / layers / motion bindings / audio cues / constraints / parameters。Recipe 不负责 render / encode / save project / execute shell。

## 11. Template 契约

Template = Style + Scene Grammar + Layer Grammar + Recipe bindings + Motion bindings + Editable Slots。**Template 不是 MP4。**

实例化后 Composition 必须独立存在。修改 Composition 不得修改 TemplateDefinition。更新 Template 不能静默改变旧 Project。项目必须保存 `originTemplateId / originTemplateVersion`。

## 12. Registry 契约

Registry 是知识定义层：可读取/可查询/可验证/可版本化/可禁用/可追溯。Registry 不是 Project database / Timeline store / Render queue / AI memory。

Registry Item 必须保存 id/version/kind/name/description/tags/enabled/commercialStatus/provenanceRef/createdAt/updatedAt。

## 13. Registry Version 契约

所有可持久化使用的 Registry Item 必须带版本（如 `motion.scale-punch@1.0.0`）。Project 保存 `recipeId + recipeVersion`。**禁止旧项目自动漂移到新版本**；Registry 更新时 old version remains addressable，除非显式 migration。

## 14. Provenance 契约

Recipe/Motion/Template/Asset/Font/Audio/Generated Asset/Reference 必须有 provenance。最少记录 origin/source/license/commercialStatus/verifiedAt/notes。商业状态只允许 `CLEAR / REVIEW / BLOCKED / UNKNOWN`。

## 15. Commercial Boundary

严格区分：代码许可证 / 素材许可证 / 字体许可证 / 音频许可证 / 品牌·商标 / 第三方作品版权 / Renderer 许可证。

**一个对象许可证明确 ≠ 其它对象自动获得授权**。尤其：`Apache-2.0 Recipe ≠ 第三方原片授权 ≠ 第三方 Audio 授权 ≠ Remotion 授权`。

本仓库边界实例：
- `src-shotcraft`（Video Shotcraft Apache-2.0）= 思想/参数吸收 CLEAR，**其仓库内 assets/（第三方音频/品牌素材）一律不进入本仓库，任何引用即 BLOCKED**
- `src-mixkit`（内置中文 BGM 若源自 Mixkit 类）= REVIEW，发布前逐曲人工核验
- `src-tts-builtin`（edge-tts 公共端点）= REVIEW，正式商用前须替换
- 用户上传 = user origin，授权由用户负责
- AI 生成 = generated origin，lineage 记录生成参数

## 16. Commercial Status 契约

```text
CLEAR   = 已满足当前系统规定的商业使用条件
REVIEW  = 仍需要人工审查
UNKNOWN = 来源/权利状态不足以作商业使用判断
BLOCKED = 系统明确禁止进入商业发布链
默认：UNKNOWN / BLOCKED → 不得进入 Commercial Publish
```

**UNKNOWN 必须允许长期存在，不能为了让系统"看起来完整"而强行变 CLEAR。**

## 17. Artifact 契约

所有生成结果必须成为 Artifact：至少含 artifactId/artifactType/version/sourceArtifactIds/inputFingerprint/outputFingerprint/createdAt/status。

## 18. Lineage 契约

任何派生 Artifact 都必须能回答：它由什么生成？用了哪个版本？哪些输入发生变化？哪些被复用？哪些失效？最少保存 `sourceArtifactIds / changedArtifactIds / staleArtifactIds / inputFingerprint / outputFingerprint`。**禁止孤儿 Artifact。**

## 19. Dependency 契约

每个可重建对象都必须有依赖关系（Scene 03 → narration-03 + asset-021 + shot.recipe-04 + motion-07 + style-contract-02）。任何修改都必须经过 Dependency Analysis。

## 20. Invalidation 契约（局部失效）

修改一个对象只能让其实际依赖失效。修改 Scene 03 Motion → 只允许 Scene 03 preview/render stale；不允许无条件整个 Project stale。修改 Style（仅视觉包装）→ 不得让 Narration/Transcript/Source Asset 失效。

## 21. Revision 契约

所有正式修改必须产生 Revision（revisionId/baseVersion/commands/affectedArtifacts/staleArtifacts/createdAt/author）。**Revision 不得覆盖历史版本。**

## 22. Version 契约

Project 下多 Variant（V1/V2/Variant-Hook-A/...）：每个 Variant 独立 composition 状态、共享不可变 artifacts。未变化的内容应允许复用。

## 23. Generation 契约

Generation Request/Result 必须记录 projectId/variantId/targetRange/type/prompt/references/providerId/firstFrame/lastFrame 与 requestId/provider/model/inputs/outputs/lineage。生成结果必须 Asset + Artifact，且可 Save/Replace/Add to Timeline/Regenerate/Delete。Provider unavailable → `GENERATION_FAILED`，**不得 fake success artifact**。

## 24. Reference 契约

Reference 是创作输入 ≠ 可直接使用的商业素材。可用于 style/composition/motion/character/product/first frame/last frame，但 **Reference ≠ 自动获得复制许可**。Reference provenance 独立保存。

## 25. Preview / Final 契约

Preview 与 Final 必须共享项目当前权威的影片状态与 revision（未迁移旧项目共享 CompositionIR revision；film.html 活动项目共享 film.html revision）。允许 preview quality ≠ final quality；**不允许 preview semantics ≠ final semantics**。

## 26. Editor 契约

用户动作：`UI → Editor Command → Server Validation → Revision → Composition Update`。**禁止 `UI → setComposition(...)` 作为正式持久化路径。**

## 27. Editor Command 契约

Command 必须含 commandId/projectId/variantId/command/targetId/payload/expectedVersion。Server 必须检查 target exists / version matches（不匹配 → **CONFLICT，禁止静默覆盖**）/ command supported / payload valid / dependency valid / commercial constraints。

## 28. Optimistic UI 契约

允许 local interaction preview / drag preview / motion parameter preview / temporary proposal preview，但正式保存必须经服务端确认。CONFLICT 时前端必须重新获取 server state、显示冲突、禁止静默覆盖。

## 29. Timeline Interaction 契约

第一版支持 select/trim/split/move/delete/duplicate/replace/lock/mute/solo/zoom/snap，必须支持 mouse/keyboard/selection/playhead/drag/snap/undo-redo。

## 30. Timeline Performance 契约

Timeline 不应把大量 clip/keyframe 全部作为高频 DOM 节点渲染。本自建 WebUI 执行口径：时间线主体用 CSS 绝对定位块 + transform（合成器友好），避免整页 reflow；playhead 移动不得触发整棵 Composition 重渲染。

## 31. API 契约

Feature 不得绕过项目统一 HTTP 入口（webui/server.py 端点）。所有 API response 必须结构化校验（服务端 pydantic/手写 schema；前端对 error 字段显式处理，不静默渲染坏数据）。

## 32. Server State 契约

Server state（Composition/Timeline/Assets/Registry/Jobs/Revisions/Review/Versions）的事实源在服务端 SQLite/JSON 落盘，**前端 JS 变量不得作为事实源**（本自建 WebUI 以服务端为唯一事实源，前端只做 transient UI state——这条在本栈下天然满足且更严格）。

## 33. Error 契约

所有错误必须可分类（VALIDATION_ERROR/NOT_FOUND/CONFLICT/DEPENDENCY_ERROR/LICENSE_ERROR/PROVENANCE_ERROR/CAPABILITY_UNAVAILABLE/PROVIDER_ERROR/RENDER_ERROR/QC_ERROR/PERSISTENCE_ERROR），带 code/message/targetId/recoverable/details。

## 34. Recovery 契约

可恢复失败必须说明：发生什么/影响什么/是否有数据丢失/下一步可做什么。**不得 `catch Exception → return success`。**

## 35. Persistence 契约

Project/Variant/Composition/Timeline/Revision/Artifact/Asset/Generation/Provenance/Review/Render Job 必须持久化。hover/drag/temporary selection/open panel 属 transient。

## 36. Restart 契约

系统重启后：Project state/Revision history/Asset lineage/Registry version/Generation history/Render job status 可恢复。不得依赖进程内存。

## 37. Provider 契约

Provider（Image/Video/Vision/TTS/ASR/Renderer）必须通过 abstraction 接入。业务层不得直接依赖具体厂商。

## 38. Workflow 契约

Workflow = Template + Editorial Grammar + Capability Selection + Provider Selection。Workflow 不得重新实现 Composition/Revision/Timeline/Rendering/Asset lineage（平台底座）。

## 39. Frontend UX 契约

所有主要页面必须完整处理 Loading/Empty/Success/Error/Retry/Disabled/Unavailable。关键操作必须提供 Preview/Confirm/Cancel/Undo。

## 40. Accessibility 契约

主要操作支持 keyboard navigation/focus state/visible control state/aria label where required。不能只通过颜色区分状态。

## 41. Reconnect 契约

Job/Render/Generation 状态查询断线后 reconnect/resync（本栈为轮询制：断线后恢复轮询即恢复状态，不得要求刷新页面）。

## 42. Commercial Publish 契约

Publish 前必须执行 Preflight，至少检查 Asset/License/Provenance/Font/Audio/Renderer/Composition validity/Missing media/Invalid references。**任何 BLOCKED/UNKNOWN 不能进入商业发布。**

## 43. Registry Content 契约

引入第三方开源内容前必须完成 Source Audit → License Audit → Attribution Audit → Modification Audit → Asset Audit。只有明确允许的内容进入 commercial registry；研究参考内容与商业发布内容隔离。

## 44. Video Shotcraft Integration 契约

Video Shotcraft 只作为 Recipe knowledge / Motion knowledge / Template grammar / Timing knowledge 进入系统。**不允许将 third-party original media / unknown audio / unknown asset / brand material / unverified demo material 标记为 CLEAR。Remotion-specific implementation 不得成为核心 Composition Contract 的必要依赖。**

## 44a. HyperFrames Integration 契约（Graphics Runtime）

HyperFrames 只作为**架构思想与 Graphics Execution Backend 的参考**进入系统（方案 §4/§84）：

- **HyperFrames ≠ Composition IR**：上层系统不感知 HyperFrames 命名；Graphics Contract（data-composition-* / data-clip-*）由本系统定义，HyperFrames 思想经 Adapter 边界吸收（未来接真实 HyperFrames Engine 时只动 adapter，不动上层）。
- **Frame 是唯一时间事实（方案 §7）**：Graphics 渲染与预览全部由 `seekFrame(frame)` 驱动；编译产物含 `Date.now/performance.now/Math.random/requestAnimationFrame/网络请求` 即 Preflight REJECT。
- **HTML 是执行格式不是 AI 知识格式（方案 §14）**：禁止 `AI → arbitrary HTML → save`；HTML 只能由 GraphicProgram 经 compiler 确定性产出。
- **沙箱（方案 §48/§49）**：Graphics HTML 一律隔离运行——渲染用 route.abort 网络阻断的 Chromium context；前端预览用 sandbox iframe（不带 allow-same-origin）。用户内容进 innerHTML 前必须转义。
- **Preview/Final Parity（方案 §55）**：预览 iframe 与正式渲染消费同一 compile_to_html 产物；允许质量差异，禁止语义差异。
- **商业边界**：Block Catalog 全部自撰 HTML/CSS（origin=own/CLEAR）；不引入第三方字体/图标/动画库（GSAP/Lottie/Three/Remotion 均不进依赖）。

## 45. Coding Agent 契约

Coding Agent 不得：扩大 Task scope / 修改无关模块 / 删除已有测试 / 绕过现有 Contract / 静默改变公共 API / 修改 Source 数据。

发现 Architecture conflict / Contract conflict / Security issue / Data migration risk / Production destructive risk → **必须停止该 Task 并报告**。普通 minor implementation issue 不得因此停止整个能力流。

## 46. Definition of Done

Task 只有同时满足 Contract + Implementation + Persistence + Failure State + Unit Tests + Integration Tests（按需）+ 变更契约文档，才可标记 PASS。

**不存在 90% complete / mostly done / almost done / 基本完成。**

## 47. 不允许的"完成"方式

不得使用："代码看起来可以"/"功能基本完成"/"UI 已经差不多"/"测试以后再补"/"先绕过 Contract"。

必须提供实际证据：command + result + artifact + test + **DOM 断言输出（本仓库替代截图的证据形态）**。

## 48. 全局工程原则

```text
Contract first        Artifact first       Lineage first
Deterministic execution   AI proposes, system executes
Source immutable      Local changes stay local
Unknown stays Unknown Version stays versioned
Preview before mutation   Human confirmation at consequential gates
E2E 测试通过 ≠ 前端落地（Jerry 铁律：必须真实浏览器 DOM 断言闭环）
```

## 49. Agent 输出格式

每次 Task 结束必须返回：

```text
TASK: <task id>
CHANGED: <files>
CONTRACT: <PASS / changed contract / none>
TESTS: <commands>
RESULT: <PASS / REJECT / MISSING / UNKNOWN>
INTEGRATION: <applicable gate>
RISKS: <none / explicit risks>
FOLLOW-UP: <only when required>
```

禁止："已经基本完成"/"应该没问题"/"后续再完善"。
