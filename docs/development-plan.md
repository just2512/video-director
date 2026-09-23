# AI Video Director 开发执行方案（v1.2 严格对齐版）

> 严格依据用户方案《创作记忆 + 参考视频分析 + 智能视觉编排增量设计 v1.2》。
> 本文件是**落地核查清单**，每项完成即勾选，不以"阶段已完成"冒充"系统完成"。

## 一、开发治理原则（v1.2 §2/§51，必须遵守）

1. 不用 "Phase 完成 = 项目完成" 管理，改用 Capability/Contract/Task/Integration Gate/Regression
2. 运行时制作有顺序（依赖关系），但步骤完成 ≠ 项目完成
3. Composition IR 是唯一创作源；用户改的是 Composition 不是 MP4
4. Draft 与 Final 分离；Human Review 是正式环节不是异常处理
5. 不再堆大型开源框架，缺能力优先薄层 Adapter/Registry/Provider

## 二、产品最终定义（§1）

AI Video Director / Personal Video Production System。输入：内容 + 素材（可选）+ 创作意图（可选）。
系统自动完成：内容理解→风格→分镜→素材理解→镜头编排→配音→字幕→BGM→SFX→Motion→Transition→Composition→Draft→人工确认→Final→QC→创作记忆。

## 三、落地核查清单（逐项，全部完成才算闭环）

### A. 体验缺陷修复（用户 8 点批评 1-7）
- [x] A1 run_webui 双开浏览器 → 自有 http.server 启动 + 单一 webbrowser.open（延迟 1.5s 待就绪）
- [x] A2 "Made with Streamlit" 广告 → 彻底移除 Streamlit，零引用（webui/server.py + static/ 自有前端）
- [x] A3 多画幅 → 9:16/16:9/1:1/3:4/4:3/21:9 六档选择
- [x] A4 无版权 SFX → 引入 shotcraft 149 条（Mixkit Free License，免署名可商用）
- [x] A5 无版权 BGM → 引入 shotcraft 5 首（Mixkit Free License）
- [x] A6 配音扩充 → 22 个（中文 15 + 英文 7，EdgeTTS 免费零 key）
- [x] A7 删除"素材语义标注 assets_semantic.json 上传框"（用户方案无此功能，重复）

### B. 创作记忆层（v1.2 §4-§16，核心护城河）
- [x] B1 CreationCase：完整创作轨迹（input/initial_plan/draft/revisions/final/feedback）— memory.CreationCase
- [x] B2 Revision Diff / Preference Delta：Draft→Final 差异提取 prefer/avoid — memory.extract_preference_delta
- [x] B3 Style DNA：抽象创作规律（六域 + compatible_with）— memory.StyleDNA/update_style_dna
- [x] B4 Like/Dislike 正式反馈信号（喜欢→保存；不喜欢→negative preference）— memory.record_feedback
- [x] B5 推荐硬匹配不评分（compatible_with 命中 → 返回风格名）— memory.recommend_style
- [x] B6 不训练模型，结构化记忆 + 规则 — sqlite3 + 规则映射表

### C. 参考视频分析层（v1.2 §13-§14/§32-§35）
- [x] C1 shot_detector：ffmpeg scene 滤镜确定性镜头边界检测（不装 PySceneDetect）
- [x] C2 frame_sampler：每镜头抽代表帧
- [x] C3 reference_analyzer：产出 reference_report.json（visual_language/shot_grammar/audio_language）
- [x] C4 风格重构不是内容复制（不复制原片画面/品牌/文字/素材）
- [x] C5 无 LLM 时启发式兜底，有 LLM 用 get_llm 增强

### D. 视觉与图片构图层（v1.2 §16/§23/§25-§31）
- [x] D1 SegmentationProvider 接口（只接口，默认不装 SAM2；明确"普通 crop+zoom 不需要"）— `video_director/segmentation_provider.py`
- [x] D2 image_analyzer 补 focal_point/negative_space 输出 — `image_editor.analyze_image`
- [x] D3 compose_image_shots：一张图产多镜头（hero/detail/macro），非 slideshow — `image_editor.compose_image_shots`
- [x] D4 VisionProvider 可替换（FrameVision 主路 + Manual 降级，已具备，确认）— `vision.py` 既有

### E. 人工确认工作台（v1.2 §40-§44）
- [x] E1 Gate A 分镜书（build_storyboard + storyboard_to_markdown）
- [x] E2 Gate B 动态草稿（render_draft 半分辨率 + WebUI 内嵌 `<video>` 播放）
- [x] E3 确认渲染（resume_after_confirm / finalize_after_review）
- [x] E4 AI 编辑与人工编辑同一份 Composition IR
- [ ] E5 自然语言修改→Composition（"把产品放大一点"→scale↑），当前只有结构化 edits，待接 LLM 自然语言解析

### F. 镜头语言与注册表（v1.2 §36）
- [x] F1 Shot Recipe 卡带完整字段（用途/适合内容/输入/motion/transition/audio/限制）
- [x] F2 已有 shot_cards（10 卡）+ registry（transition/motion）+ audio（SFX/BGM 分类）

## 四、明确不做（§53，防止淹没核心）
不装本地视觉大模型/向量数据库/训练模型/自研视觉/TTS/抠图/完整剪映级编辑器/十几个独立 Agent/新大型框架。

## 五、验收闭环
1. 三 Agent 产物各自 pytest 通过 + 主控抽查
2. 26 单测零回归
3. 自有前端 Playwright 真实浏览器闭环（单视频直通 / 多视频直通 / 预览确认 三测全绿：无 Streamlit + 视频播放器 + final.mp4 + QC PASS）
4. main 合并 git 干净