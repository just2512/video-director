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
| 前端 React+Zustand+TanStack | 自建零框架 WebUI（webui/static/index.html + webui/server.py，Python stdlib http.server）。**禁止引入 React/Vite/npm 栈**——方案 Task Card §0.3 原文明确"如果现有项目已有同等文件，应扩展而不是重复创建" |
| `npm run typecheck / test / build` | `python -m pytest tests/ -q`（单测/契约）+ `python tools/test_webui_*.py`（Playwright DOM 断言 E2E） |
| 验收证据 "screenshot" | **禁止截图**（Jerry 全局规则）。UI Gate 证据 = Playwright DOM 断言输出（断言文本 + 元素存在性） |
| frontend/src/api/client.ts | WebUI 内 `fetch()` 统一走 `webui/server.py` 端点；前端不直接拼第三方 URL |

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

## 6. Composition IR 是唯一创作事实源

Editor、AI、Template、Recipe、Generation 都不得直接修改最终 Render 文件。

统一：`Command → Composition IR → Revision → Dependency Analysis → Render`。

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

Preview 与 Final 必须共享 Composition IR。允许 preview quality ≠ final quality；**不允许 preview semantics ≠ final semantics**。

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
