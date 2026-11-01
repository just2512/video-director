# Creative OS 集成验收闸门（INTEGRATION-GATES）

> 改编自《全局开发契约与集成验收闸门.md》INTEGRATION-GATES.md 部分，适配本仓库栈。
> Gate 不是开发阶段、不是批次。多个 Capability 并行开发；发生集成时必须通过对应 Gate。
> 验收命令统一为本仓库口径：`python -m pytest tests/ -q -k <关键词>` + `python tools/test_webui_shotcraft.py`（Playwright DOM 断言）。

---

## 1. Gate 状态

只允许 `PASS / REJECT / MISSING / UNKNOWN`。

- PASS = 当前证据满足 CONTRACT.md
- REJECT = 已验证违反 CONTRACT.md
- MISSING = 应有证据但尚未提供
- UNKNOWN = 当前证据不足以做结论

**不得使用 90% / 95% / 基本通过 / 大体没问题。**

## 2. Gate Evidence

每个 Gate 必须提供：Task IDs / Commit / 测试命令 / 测试结果 / 相关 artifact / 拒绝时的失败证据。

UI Gate 证据 = **Playwright DOM 断言输出**（断言清单 + 每条 true/false）。本仓库禁止截图，DOM 断言为 UI 验收唯一证据形态。

---

## IG-REG Registry Gate

**Scope**: REG-001~005（registries/core/provenance/recipes/templates + builtin 数据）

Checks:
- Schema：每个 Item 可被 schema 验证（非法 schema 被拒）
- Version：同 id 多版本可共存
- Provenance：所有 CLEAR/REVIEW Item 存在 provenance_ref
- Commercial Status：只出现四态
- Duplicate：重复 (id,version) 被拒
- 禁止字段：ffmpeg_command/shell_command/remotion_component 等出现即拒

命令：`python -m pytest tests/ -q -k registry`

## IG-PROV Provenance / Commercial Gate

**Scope**: REG-002 + Asset/Recipe/Motion/Template provenance

测试矩阵：

| Input | Expected |
|---|---|
| own + verified | CLEAR |
| Apache code + compliant notice | CLEAR |
| third-party + license unclear | UNKNOWN / REVIEW |
| blocked material | BLOCKED |
| missing source | UNKNOWN |

硬检查：`Apache source ≠ third-party source asset`；`Recipe license ≠ Asset license`；`Renderer license ≠ Template license`。

命令：`python -m pytest tests/ -q -k provenance`

## IG-SHOT ShotRecipe Gate

**Scope**: REG-003 / RES-001 / CMP-001 / EDT-003（前端 Shot Picker）

Flow: `Shot Recipe → Resolve → Pick → Instantiate → Scene → Timeline → Preview`

Checks:
- Resolve 输入 product_introduction + product image + 4s + editorial → 至少一个 SUPPORTED 或明确 MISSING_REQUIREMENT，不能无理由成功
- Instantiate 产出 Scene 必须带 originRecipeId/originRecipeVersion
- 用户替换 Shot 后只有 Scene 03 changed，不得让 Narration 01/Scene 01/02/无关 Assets 失效
- 前端：分镜卡"换镜头"弹层能加载真实 resolver 候选并应用

命令：`python -m pytest tests/ -q -k shot` + `python tools/test_webui_shotcraft.py`（shot_picker 断言组）

## IG-MOTION Motion Gate

**Scope**: REG-004 / RES-002 / CMP-002 / EDT-002 / EDT-007

Flow: `Layer → Motion Gallery → Preview → Apply → Timeline → Preview Runtime → Final Render`

Checks:
- Compatibility：subtitle + camera motion → REJECT/INCOMPATIBLE（UI 灰掉而非渲染才失败）
- Preview：Motion Gallery 每卡 CSS 循环预览渲染
- Apply：点击应用 → Revision created + Composition changed（Layer.motion_recipe_id 落库）
- Cancel：Preview → Cancel → Composition unchanged + Revision unchanged
- Version：项目引用 motion.foo@1.0.0，Registry 出 1.1.0 后旧项目仍用 1.0.0

命令：`python -m pytest tests/ -q -k motion` + `python tools/test_webui_shotcraft.py`（motion 断言组）

## IG-TEMPLATE Template Gate

**Scope**: REG-005 / RES-003 / CMP-003 / EDT-004

Flow: `Template Gallery → Preview → Use → Instantiate → Composition → Timeline → Edit → Save → Reopen`

Checks:
- Instantiate 生成 Composition 且记录 originTemplateId/originTemplateVersion
- Editable：可 change text / replace image / change motion / change duration
- Isolation：修改实例 Composition 不改变 TemplateDefinition
- Reopen：关闭重开保持 same composition / same template lineage / same recipe versions

命令：`python -m pytest tests/ -q -k template` + `python tools/test_webui_shotcraft.py`（template 断言组）

## IG-EDITOR Editor Gate

**Scope**: EDT-001 / EDT-005 / EDT-006 / EDT-007

Full User Flow: `Open Project → Select Scene → Select Layer → Inspector → Modify Motion → Timeline Trim/Move → Preview → Save → Reopen`

Checks:
- Selection：点击 Timeline Layer → Inspector 同步
- Inspector 显示实际 server state
- Timeline 至少验证 select/trim/split/move/delete
- Persistence：Edit → Save → close → reopen 修改保持
- Concurrency：version N 下发 command，server 已变 N+1 → **CONFLICT，不静默覆盖**

命令：`python -m pytest tests/ -q -k editor` + `python tools/test_webui_shotcraft.py`（editor 断言组）

## IG-GENERATION Timeline-native Generation Gate

**Scope**: EDT-008 + Generation API + Asset System + Lineage

Checks:
- Generation 绑定 project/variant/start/duration
- Result 记录 generationRequestId/providerId/inputFingerprint/outputFingerprint/sourceArtifactIds
- Add to Timeline 产生 Asset + Artifact + Layer + Lineage
- Provider unavailable → GENERATION_FAILED，无 fake success

命令：`python -m pytest tests/ -q -k generation`

## IG-REVISION Revision / Proposal / Patch Gate

**Scope**: EDT-009 / REV-001 / REV-002；FilmDocument 的 AI 提案审批与 Revision 冲突边界

Required cases:
1. **Reject**：自然语言 → Proposal → Reject → Composition / FilmDocument unchanged + Revision unchanged + Artifact unchanged
2. **Preview**：生成 Proposal 只生成候选文档；不得提前写入正式 FilmDocument
3. **Apply authority**：客户端只提交 `project_id / proposal_id / base_revision`；操作内容必须来自服务端登记的 Proposal Registry。客户端附带的 `ops` 不得覆盖服务端操作
4. **Exact base**：只允许原始 `base_revision` 精确匹配时应用；revision 已变化必须 CONFLICT，不静默 rebase
5. **Single-use**：同一 proposal 成功应用后再次提交必须返回 HTTP 409 `PROPOSAL_NOT_FOUND_OR_STALE`；第二次请求不得新增 revision 或改写 `film.html`
6. **Concurrent apply**：两个 HTTP 请求并发确认同一 proposal，必须恰好一个 HTTP 200、另一个 HTTP 409；最终只产生一组登记操作和一次有效 revision 变更
7. **Locality**：修改 Scene 03 Motion → 只有 Scene 03 render stale，不得整项目 stale
8. **Ambiguous Target**："把它改一下" → MISSING_TARGET → 不执行

命令：
- `python -m pytest tests/test_film_document_contract.py -q -k 'proposal or apply_proposal'`
- `python -m pytest tests/test_film_studio_ai_proposal_browser.py -q`
- `python -m pytest tests/test_film_studio_generated_asset_adoption_browser.py -q`

UI Gate 仍要求 Playwright DOM 断言；真实模型调用使用单独的 opt-in 验收，不得把 mock SSE 测试认作真实 Provider PASS。具体模型验收要求见 [Live Model Stream Acceptance](docs/live-model-stream-acceptance.md)。


## IG-PREVIEW Preview / Render Consistency Gate

相同 Composition 的 Preview 与 Final Render 必须使用 same scene order / durations / layer relationships / motion semantics。允许分辨率/质量差异；不允许编辑语义差异。

命令：`python -m pytest tests/ -q -k preview_render`

## IG-PREFLIGHT Commercial Preflight Gate

测试矩阵：

| Asset | Import→Timeline→Render | Commercial Publish |
|---|---|---|
| CLEAR | PASS | 允许 |
| UNKNOWN | 允许编辑 | **REJECT** |
| BLOCKED | 允许编辑 | **REJECT** |
| REVIEW | 允许编辑 | 显示明确状态（不得显示 CLEAR） |

特别测试：`Motion Recipe = CLEAR + Asset = UNKNOWN → 仍然 REJECT`（不因 Recipe 授权明确而放过 Asset）。

前端：REJECT 时显示 issue 清单 + [定位素材]。

命令：`python -m pytest tests/ -q -k preflight` + `python tools/test_webui_shotcraft.py`（preflight 断言组）

## IG-VERSION Version Stability Gate

创建项目引用 `motion.foo@1.0.0` → Registry 新增 `motion.foo@1.1.0` → 打开旧项目 → 仍用 1.0.0。漂移即 REJECT。Template 同理。

命令：`python -m pytest tests/ -q -k version`

## IG-PERSIST Persistence / Restart Gate

Flow: `Create → Template → Shot → Motion → Generate → Edit → Revision → Save → Restart → Reopen`

必须持久化：Composition/Timeline/Revision/Asset/Generation/Lineage/Registry versions/Provenance。不得依赖进程内存。

命令：`python -m pytest tests/ -q -k persistence`

## IG-UI Frontend Interaction Gate

每个主要组件必须实际实现：Loading / Empty / Loaded / Error / Retry / Disabled / Unavailable。

必须交互：Mouse / Keyboard / Selection / Esc / Enter / Undo / Redo / Cancel。

- Motion Gallery：search / filter / preview / apply / cancel
- Template Gallery：preview / use / cancel
- Inspector：edit / preview / apply / cancel
- 分镜卡：换镜头 / 换视觉 / 生成素材 / 调整
- 首页：9 新建入口 + 待处理三态 + 封面卡片
- 素材库：License 徽标四态
- Versions：版本列表 + 两版本 Compare

证据：Playwright DOM 断言全组通过。

## IG-RECONNECT Reconnect Gate

Job running → 断开 → 重连（恢复轮询）→ current job status recovered；不得 duplicate job / fake failure / lose job state。

## IG-REGRESSION Full Regression Gate

内容：Talking Head / Product Video / Screen Demo / Long Video / Reference Video / Image Story。
编辑：Change Hook / Change Style / Replace Scene 03 / Add B-roll / Remove PiP / Change Subtitle Style / Lower BGM / Extend Scene 04。
生成：Regenerate Scene / Replace Generated Asset / Generate image/video/SFX。
时间线：Drag playhead / Trim / Split / Move / Delete / Duplicate / Zoom / Snap。
渲染：Draft / Final / Reopen old Project / 16:9 / 9:16 / 1:1。

执行口径：既有回归套件（test_webui_creative_os / debt_closeout / revision / full_plan 等）全部保持绿。

## IG-E2E Commercial Creative E2E Gate（系统最重要综合闸门）

Scenario:
```text
Create Project → Choose Template → Instantiate Composition
→ AI Generate Storyboard → Choose Shot Recipe for Scene 03
→ Choose Motion for Title → Edit Timeline
→ Generate Image into selected range → Add to Timeline
→ AI: "第二段换成产品近景，标题慢一点，字幕小一点"
→ Proposal → Preview → Apply → Local Rebuild → Draft Render
→ Commercial Preflight → Final Render → Reopen Project
```

Required Evidence：Template lineage preserved / Shot lineage preserved / Motion lineage preserved / Generation lineage preserved / Revision persisted / Affected artifacts correct / Unchanged artifacts reused / License status preserved / Preview valid / Final render valid / Project reopen valid。

证据形态：`tools/test_webui_shotcraft.py` 全组 DOM 断言 PASS + pytest 全绿。

## IG-SHOTCRAFT Video Shotcraft Integration Gate

至少一个 ShotRecipe + 一个 MotionRecipe + 一个 Template 完成 `Registry → Resolver → Composition → Timeline → Preview → Revision → Render` 全链。

Commercial Audit：每个进入 Commercial Registry 的对象必须能回答：来源是什么？许可证是什么？是否进行了修改？是否包含第三方原始素材？是否允许当前商业使用？

**Remotion Boundary**：ShotRecipe → Composition 不得要求 Remotion Runtime 才能成立。Motion Contract 必须与具体 Renderer 解耦。

## IG-GFX Graphics Runtime Gate（HyperFrames 集成）

**Scope**: GFX-001~007（Contract/Program/Compiler/Frame Seek/捕获/编码）

Flow: `Text → Graphic Plan → Block Resolve → GraphicProgram → HTML → Preflight → Preview → Frame Capture → MP4`

Checks:
- 确定性：编译 HTML 含 window.__gf.seek(frame)；preflight 拒收 Date.now/Math.random/fetch/外链
- 沙箱：渲染页网络全阻断（route.abort），preview 用 sandbox iframe 隔离
- Parity：preview 与 final 消费同一 compile_to_html 产物
- 失败诚实：playwright 缺失 → RENDERER_UNAVAILABLE（禁 fake success）

命令：`python -m pytest tests/ -q -k graphics` + `python tools/test_webui_hyperframes.py`

## IG-BLOCK Block Gate

Flow: `Block → Plan 绑定 → 前端换块 → HTML → 预览 → 渲染`

Checks:
- Catalog 自撰（origin=own/CLEAR），无第三方字体/图标/动画库
- Resolver 三态（SUPPORTED/MISSING_REQUIREMENT/INCOMPATIBLE），零评分
- 用户内容进 innerHTML 前必须转义（XSS 边界）

命令：`python -m pytest tests/ -q -k graphics`（blocks/resolver 组）

## IG-TEXT-TO-GRAPHIC 文案→图形 Gate

输入纯文案 → 输出 Graphic Plan（场景数/角色/块绑定/时长）→ 可预览 → 可渲染。
不允许 `AI → arbitrary HTML → save`（HTML 只能经 compiler 产出）。

命令：`python -m pytest tests/ -q -k graphics` + `python tools/test_webui_hyperframes.py`（plan/preview/render 断言组）

## IG-HTML-SANDBOX 沙箱 Gate

测试：生成 HTML 不可发起网络请求/访问主 App DOM/cookie；preflight 拒收网络标记。

命令：`python -m pytest tests/ -q -k preflight`（graphics preflight 组）

## IG-FINAL Final System Gate

所有 Gate 同时通过才允许声明 Commercial E2E = PASS。任何关键 Gate REJECT → Commercial E2E = REJECT；任何关键证据 MISSING → Commercial E2E = MISSING。**不能通过"整体看起来正常"覆盖单个关键 Gate。**

## Gate 执行原则

```text
Capability 可以并行开发 → Contract 持续稳定 → 局部 Integration Gate
→ 能力继续并行 → Full Integration → Regression → Commercial E2E
```

这里没有第一批/第二批/Phase。只有 Capability → Contract → Task → Integration Gate → Regression。

## Agent 输出格式

每次 Task 结束按 CONTRACT.md §49 格式输出（TASK/CHANGED/CONTRACT/TESTS/RESULT/INTEGRATION/RISKS/FOLLOW-UP），禁止"基本完成"。
