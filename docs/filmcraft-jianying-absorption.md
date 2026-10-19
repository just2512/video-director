# 剪映×filmcraft 指纹级功能差异分析与吸收方案 — video-director

> 方法：逐条对照两份剪映逆向报告（v1 架构 + v2 深挖变速/关键帧/吸附）与 filmcraft-main
> 参考项目源码（Rust：time/project/edit/transitions/keyframe crate），与我系统逐项
> **能力实现细节**对比。不做功能名重叠即等价的判断，每条核实数据模型+操作语义+前端交互。

---

## 一、核心发现：三个结构性差异（非功能补丁级）

### D1. 时间基：float 秒 vs 整数 Tick 【根因级】
- **filmcraft**：`Tick(i64)`，254,016,000,000 ticks/s——整除 23.976/24/25/29.97/30/60/120
  全部广播帧率与 8k–192k 全部音频采样率。所有编辑数学是整数运算，零累积误差。
- **我系统**：`float` 秒。30fps 下 1 帧周期 = 1/30 = 0.0333… 是无限循环二进制小数，
  浮点比较靠 `abs(x-y)<1e-6` 容差散布全库（editor.py 内 40+ 处）。PR #82+#84 的
  「项目帧时钟」本质是在浮点层上模拟整数帧——`_project_frame_time` 每 8 个命令重复调用，
  漏一处就是一颗雷（本轮对抗审查已实锤 3 处漏网）。
- **吸收判断**：整体迁移到 Tick 是伤筋动骨（Pydantic IR + FFmpeg 层 + 前端全链），
  [成本高/风险高]。**不整体迁移**。但吸收两个低垂果实：
  1. `_project_frame_time` 收敛为编辑命令入口的统一前置（在 `_execute_command_unlocked`
     分发前对含时间 payload 的命令做一次集中 snap——从"每命令记得 snap"改
     为"入口统一 snap"，消灭漏网类），边界命令白名单例外。
  2. 前端 `TimelineEditMath.projectFrameTime` 已对齐（PR #82 已做），保持。

### D2. 关键帧存储：clip-relative vs 我系统同样是相对时间但插值语义薄
- **filmcraft**：`Keyframe{time: Tick(clip相对), value: ParamValue(7种), interp: 7种插值,
  out_influence/in_influence}`——Bezier/AutoBezier/ContinuousBezier/EaseIn/EaseOut 带
  influence 手柄；`Param` 支持静态值或关键帧轨双形态；「trim/split 不需移动关键帧」
  是数据模型设计目标（lib.rs 文档明说）。
- **我系统**：`KeyframeTrack{property, keyframes[{at(相对层秒), value: Any, easing(4种)}]}`。
  `at` 也是相对层——这点对齐。差异在：插值只有 linear/ease_in/ease_out/ease_in_out
  4 种（无 Bezier 手柄），value 无类型约束（Any），无 keyframe 编辑交互（只能 add/remove）。
- **吸收判断**：easing 扩展（加 Bezier + influence）是渲染器配套改造 [成本中]。
  value 类型化是 schema 卫生 [成本低]。**本轮吸收**：不动数据模型，补交互缺口
  （旧 CompositionIR 的通用 Bezier 关键帧仍未接入；film.html 路径的音量包络已经可拖动/改值，见下文更新）。

### D3. 编辑代数完整性：Premiere 语义 vs 我系统子集
filmcraft edit crate 实现完整 Premiere 语义：Overwrite/Insert（sync-lock 联动 ripple）/
Lift（留隙）/Extract（闭隙 ripple）/Ripple delete/Trim×5（regular/ripple/roll/slip/
slide/rate-stretch）/through-edit 检测+愈合/join。我系统对照：
| filmcraft 操作 | 我系统对应 | 差距 |
|---|---|---|
| Overwrite | add_visual_overlay/place | 有 |
| Insert + sync-lock ripple | add_visual_overlay placement_mode=insert（只延长时间线，不推后内容） | **半成品**：insert 不推挤已有片段 |
| Lift（留隙） | delete + gap（PR 已有显式 gap 层） | 有 |
| Extract（闭隙 ripple delete） | **无** | 缺 |
| Roll trim（切口两侧互让） | film.html Studio 已有同源连续切口 Ctrl 拖动 | 已落地；限制同源、速度一致、无 fade |
| Slip trim（源区间滑动不动时间线） | film.html Studio 对视频/音频 Alt 拖动 | 已落地；固定 timeline 入点与长度 |
| Slide trim | **无** | 缺 |
| Rate stretch trim | speed 手改（无拖拽联动） | 弱 |
| Through-edit 检测+愈合 | film.html Studio 的“愈合切口”操作 | 已落地；要求同轨相邻、源区间连续和属性兼容 |
| 转场对齐模型 | Scene.transition_out{type,duration} | 缺 Center/Start/End/Custom + 拖边缘改时长 |

---

## 二、交互层差异（剪映 v2 报告逐条核实）

### D4. 变速交互：双模式（线性预设档+曲线）vs 单一 number input
- **剪映**：片段内联 SpeedControlView（30KB QML）：平均速度按钮 + 0.1–10× 预设档 Popup +
  曲线模式（GradientControl 拖拽把手中点）+ 「先校验再落值」单一入口
  checkAndToastBeforeChangeSpeed() + 浮层实时显示「0.5x ~ 2.0x」区间。
- **我系统**：visual-overlay-editor 的 `<input type=number min=0.25 max=4 step=0.05>`。
  无预设档、无曲线、无片段内联。
- **吸收**：曲线变速是引擎级改造（需逐帧速度映射）[成本高]，**不吸收**。
  吸收**预设档 + 前置校验统一入口**：speed 输入改预设档 chips（0.5/1/1.5/2/4×）
  + 自定义输入框；前端校验统一走一个函数（对齐"checkAndToast"思想）。

### D5. 关键帧交互：点表批量下发 + 激活三态 vs 完全无时间线关键帧 UI
- **剪映**：片段上音量包络线（QSG 自绘）+ 点表批量下发（QML 不持模型）+ 播放头扫过
  高亮（Activated 三态）+ record 参数（程序化补偿不污染 undo）。
- **旧 CompositionIR 路径**：仍有 add_audio_keyframe/add_keyframe 命令，但没有通用时间线关键帧 UI；OpenFilm film.html 路径现在已经有独立音量曲线模式和点编辑。
- **吸收（film.html）**：音频/视频片段上渲染可编辑 Canvas 音量包络；点添加、拖动、删除写成 clip-relative volume_envelope 属性；同一曲线驱动预览与 FFmpeg 导出。旧 CompositionIR 的 Bezier/通用属性关键帧仍是独立工作。

### D6. 吸附交互：吸附纯函数 + 显式吸附区 vs 播放头吸附已有、片段吸附缺
- **剪映**：三类吸附（播放头/主轨/音频节拍）+ bottomAdsorb 显式区域（拖音频靠近底部
  变轨区，节拍线 reparent 搬场）+ `adsorbed` 纯状态（UI 只读结果改视觉）。
- **我系统**：播放头吸附完整（editorSessionSnapTime + Guide + Shift 旁路）。片段拖拽
  **不吸附**（无磁贴）。
- **吸收**：拖拽片段时对齐候选点=播放头位置+片段边界，吸附态改变 UI（描边高亮）。
  复用现有 snap 引擎加 clip-boundary 候选源。[成本中]

### D7. 拖拽边缘自动滚动：4 个专用 ScrollTimer vs 无
- **剪映**：拖到 Flickable 边缘 40px → 方向 0-3 自动滚动 + 每 tick 重算增量。
  每种拖拽（cursor/drag/cut/box-select）独立 Timer。
- **我系统**：完全无。拖片段到可视区边缘就卡住，必须先松手滚动再拖。
- **吸收**：实现一个通用边缘自动滚动器（pointermove 检测边界 40px 带 → rAF 循环滚动
  + 重算拖拽增量），挂到现有 5 个拖拽入口。[成本低，交互收益高]

### D8. 时间线缩放：完全无入口
- 我系统 ED.zoom 存在但无任何 UI（无滚轮/无按钮）——发现的真实缺陷。
- **吸收**：Ctrl+滚轮缩放（以指针为锚点）+ 缩放按钮。同时把 zoom 的 px/s 映射对齐
  _tlPlayheadLeft。[成本低]

### D9. 触控板识别（wheel angleDelta 模式判断）
- 剪映源码注释级证据：`angleDelta % 120 !== 0 && 无 modifiers = 触控板平移`。
- 我系统无 wheel 处理。随 D8 一并实现（触控板=平移时间线，Ctrl+滚轮=缩放）。

---

## 三、已核实等价、不吸收的项（防功能堆砌）

| 剪映/filmcraft 能力 | 我系统现状 | 判定 |
|---|---|---|
| record 参数贯穿 + draftAmend | 前端 pointerup 才发命令，天然一手势一 undo 单元 | **等价，不吸收** |
| pointercancel→Finished 收尾 | 5 处拖拽全部有 pointercancel 处理 | **等价，不吸收** |
| source/target_timerange 分离 | Layer.source_in/source_out + start/duration 双分离 | **等价，不吸收** |
| draft_content.json materials/tracks 分离 | assets 池 + scenes/visual_overlays/audio 分离 | **等价，不吸收** |
| 双 NPU 推理路径 | 不适用（LLM 云端） | 不吸收 |
| QSG 高性能自绘 | DOM/SVG 够用（规模小一个量级） | 不吸收 |
| multicam/captions/transcript 模块 | 超出产品定位 | 不吸收 |

---

## 四、本轮吸收实施清单（融合而非堆砌）

按「前端用户交互」优先 + 与现有功能交叉融合排序：

| # | 改动 | 融合点 | 层 |
|---|---|---|---|
| A1 | 时间线缩放（Ctrl+滚轮以指针为锚 + ±按钮） | ED.zoom 接入 _tlPlayheadLeft 现有映射 | 前端 |
| A2 | 拖拽边缘自动滚动器 | 挂现有 5 个 pointermove 拖拽（playhead/clip/audio/overlay/watermark） | 前端 |
| A3 | 片段拖拽磁贴吸附（播放头+邻块边界候选，吸附态高亮） | 复用 editorSessionSnapTime 引擎加候选源 | 前端 |
| A4 | 变速预设档 chips + 统一前置校验 | film.html Studio 已有 0.5/1/1.5/2/4× chips；旧 CompositionIR editor 仍独立 | 前端分路径落地 |
| A5 | film.html 音量包络折线及关键帧编辑（volume_envelope→Canvas） | 已落地；split/trim/ripple 会切片包络，FFmpeg 与预览共用线性曲线 | 前端+文档契约 |
| A6 | `_project_frame_time` 入口统一（分发层集中 snap 时间 payload） | 消灭「漏 snap」类 bug 的根因；保留 per-command snap 兜底 | 后端 |
| A7 | through-edit 检测（split 后相邻段同源连续→标记）+ join_through_edits 命令 | film.html 已有 guarded join 操作；legacy CompositionIR path 尚独立 | 分路径落地 |
| A8 | Roll/Slip trim | film.html 已有 Ctrl-roll 与 Alt-slip；roll 暂限同源连续、等速切口；Slide trim 仍待补 | film.html 前端+后端已落地一部分 |

A1–A5 前端交互、A6 后端防御、A7 真功能补全。A8 暂缓防堆砌。

---

## 五、Playwright 实测新发现的两个真实缺陷（非吸收项，属既有缺陷）

吸收实现完成后按 Jerry 指令真启动浏览器实测，抓到两个**与吸收无关的既有缺陷**：

### D10. 上传素材在编辑器里整体失能（严重）
- **症状**：项目里 `local-*` 上传素材显示裸 ID、无缩略图；「加画面叠加」「移到播放头轨道」「展开为自由时间线」「拆分原声轨」四个入口全部 `ASSET_NOT_FOUND`。
- **根因**：上传素材只登记在**项目** `assets/manifest.json`，不进**全局 Library**（`asset_catalog`）；而 `_editor_asset_preview_map` 与 `_overlay_source_duration` 都只查 Library。
- **修复**：两条路径统一为 **IR → Library → 项目 manifest** 三级回落（新增 `_asset_ref_from_project_manifest`）。
- **实测**：素材表 1 → 9 条；真实上传 mp4 的 `add_visual_overlay` `ASSET_NOT_FOUND` → `APPLIED`。
- **为何 CI 没抓到**：单测直接构造 `ir.assets`，从不走「只有项目 manifest」的上传路径——正是「代码完成≠系统完成」的典型。

### D11. 编辑器版本号与服务端脱节 → 撤销后所有保存失能（严重）
- **症状**：做过若干次编辑+撤销后，浏览器里任何编辑操作都被 `CONFLICT` 拒绝（实测客户端 9 vs 服务端 11），只有整页刷新才恢复。
- **根因**：`/api/editor/composition` 返回 `len(revs) + 1`，而命令冲突检查用 `editor.composition_version()`（= `max(len(revs)+1, 持久化版本)`）。撤销历史累积后两者脱节。
- **修复**：端点改回传 `composition_version(pid)`（与冲突检查同源，单一真相）。
- **发现路径**：A7 愈合在 worktree 构建上通过、在部署构建上失败——差异定位到版本错位，而非愈合逻辑本身。

**教训合流**：D10/D11 都只有在「真浏览器 + 真数据 + 合并后产物」下才暴露。CI 全绿、单测全绿都与它们无关。

