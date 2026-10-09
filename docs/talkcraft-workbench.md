# 口播导演工作区：语义拍与镜头计划

本模块将口播创作计划接进现有 Video Director 工作台，不另建一套 Composition 或选择状态。

## 用户操作

1. 在“工作台”打开一个已有 CompositionIR 的项目。
2. 打开“口播导演 · 语义拍与镜头计划”，点击“生成 / 刷新蓝图”。
3. 逐句核对语义标签、实体/证据线索、画面需求、主句/陪衬句、叙事意图和画面意图。
4. 点击“保存标注”。保存会将场景意图写入 SceneIR，并经过现有 Editor Command → Revision 版本链。页面不直接写 CompositionIR。
5. 为镜头选择动效配方。先“保存配方到计划”只会持久化选择；点击“应用到所选图层”才会运行现有 visual.motion Action/Command 路径，修改真实时间线。
6. 回到时间线预览并继续编辑。版本冲突会拒绝写入，要求刷新后重试。

## 持久化产物

- alignment/character_timing.json：按当前词级时间戳展开的逐字时间网格。
- plans/semantic_beats.json：逐句语义标注、实体、画面需求、review 状态和输入指纹。
- plans/shot_plan.json：以现有 SceneIR 场景区间为边界的镜头计划、图层和配方选择。

CompositionIR 的现有 Scene 起止时间仍是镜头范围的权威来源。计划文件是可编辑的补充合同，不得另行决定场景时间。输入指纹覆盖场景 ID、文本、时长、帧率和词级时间戳文件；稿件/时间轴或时间戳改变后，原蓝图变为只读 STALE，不会默默套用旧标注。

## 时间戳诚实边界

当前服务不冒充强制对齐器。它消费 alignment/words.json 中已有的 {text,start,end}，并把词段时间展开到字符；未匹配字符用相邻锚点插值。每个字符的 timing_source 会标明 interpolated_from_word、interpolated_between_anchors 或 estimated_from_scene。没有足够的词级锚点时，句子会标记 UNKNOWN / PARTIAL 且要求复核。

因此，本模块交付的是可追溯的逐字时间网格，不宣称所有中文字符都经过独立声学强制对齐。正式逐字对齐 Provider 尚未接入时，前端会持续显示这个边界。

## 配方应用的真实能力门

配方候选先读取当前 Motion Registry，再按目标图层类型与真实 FFmpeg 通道消费限制过滤：
- video 当前仅允许 opacity 动画。
- image 当前允许 opacity 与 scale。
- 其他图层不会被误报为当前通用 Motion Recipe 渲染器支持。

当前 FFmpeg 实现不消费的运动通道会在应用前被拒绝。不能因为 Registry 声明了兼容层就假设渲染器一定能执行。

## HTTP API

- GET /api/talkcraft/plan?project_id=...
- POST /api/talkcraft/prepare
- POST /api/talkcraft/beat
- POST /api/talkcraft/recipe/plan
- POST /api/talkcraft/recipe/apply

错误以 {ok:false,error} 返回；版本冲突用 HTTP 409，不覆盖用户新版本。

## 测试

tests/test_talkcraft_planning.py 覆盖逐字时间网格来源标记、无锚点降级、语义启发式、原子 JSON 写入、配方清单序列化与 Scene intent 命令校验。

## 许可证边界

此实现是 Video Director 的原创服务和 UI，不复制 video-talkcraft 的 TSX、卡片、脚本或素材。仅吸收公开描述的工作流概念；如需复用第三方仓库受限代码，必须先获得相应授权。
