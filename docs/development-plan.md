# AI Video Director 统一开发执行方案

> 收敛自多轮补充讨论（V2/Hypit/视频理解/SVF/jianying-headless/guizang/OpenMontage/
> shotcraft）与用户多轮批评指正。
>
> **架构总原则（用户 2026-09-20 裁决，纠正此前误读）**：
> 全部规划能力都要做。依赖视频理解模型的功能，**视频理解只是兜底方案，
> 优先用其他替代方案实现**（文件名语义/概念桥接/手工标注/抽帧等零成本路径）；
> 不是"有能力才启用"。**常规大模型（LLM）必须配备**——LLM Provider 是标配，
> 只有"视频理解"类不作为主力。

## 一、产品承诺（用户视角的唯一验收标准）

1. 一句话文案 + 素材（可选）→ 自动成片
2. 核心全链路零视频理解模型可跑（替代方案优先）；LLM 增强是标配路径
3. 按文案内容匹配画面（不是重复循环）
4. 一键启动 WebUI（`python run_webui.py` 直接可开）
5. **成片前有人工调整编辑确认环节**（不确定性高的中间产物先给用户过目）
6. 四条产品线：文案成片 / 图片智能编辑成动画 / 产品图生成广告片 / 视频复刻

## 二、已落地（对应 commit，不再返工）

| 能力 | 位置 |
|---|---|
| Composition IR | models.py |
| 语义匹配（文件名/概念桥接/手工标注，视频理解兜底） | matcher.py |
| Style Contract 四风格 AUTO | style.py |
| TTS 词级时间戳（EdgeTTS 零 key） | narration.py |
| 字幕烧录 | subtitle.py / renderer.py |
| FFmpeg 确定性渲染 + BGM/SFX ducking | renderer.py / audio.py |
| 七项确定性 QC + ≤3 修复循环 | qc.py / pipeline.py |
| 任务状态机 + probe 缓存 | store.py |
| doctor + capability gate + run manifest + 失败二分 | doctor.py / runlog.py |
| SFX 动作绑定 | audio.py |
| SCRIPT_ONLY 纯文案模式（零素材成片） | script_only.py |
| Pipeline Manifest + Checkpoint | pipeline_defs.py / checkpoint.py |
| Capability Registry | capabilities.py |
| 独立项目 + 一键启动器 | run_webui.py |
| 前端真实测试（Playwright DOM 断言） | tools/test_webui_*.py |

## 三、能力分层（纠正版：全部要做，分层实现）

| 能力层 | 实现策略 | 状态 |
|---|---|---|
| **确定性基座** | FFmpeg/规则/概念桥接/文件名语义——零外部依赖可用 | ✅ 已落地 |
| **LLM 增强（标配）** | 常规大模型 Provider（文案优化/分镜规划/风格判断/复刻拆解），有 key 即用、无 key 诚实降级到规则路径 | ⬜ 本方案落地 |
| **图片理解** | 替代方案优先：EXIF/颜色直方图/构图九宫格/显著性采样（纯 Python）→ 手工标注 → LLM 视觉（兜底） | ⬜ 本方案落地 |
| **视频理解** | 替代方案优先：抽帧→图片路径 / 转写音频→文本路径 → LLM 视觉（兜底） | ⬜ 后续 |
| **Visual QC** | 确定性 QC 已有；LLM 看片复核在 LLM Provider 落地后接 | ⬜ 随 LLM 落地 |

## 四、产品线规划（用户三条新需求 + 成熟化建议）

### 线 1：图片智能编辑成动画（已明确，本轮落地）
用户给图片 + 文案 → 不是简单排序拼接，而是：
- AI 分析图片（替代方案：构图分析/主色提取/显著性区域裁剪，LLM 视觉兜底）
- 裁剪关键元素 + 文字排版 + Ken Burns/zoom/pan 特效 + 转场
- 渲染成动画视频

### 线 2：产品图生成广告宣传片（已明确，本轮落地）
实物产品几张图 → 广告片。借 shotcraft 成熟方法论：
- **镜头配方卡体系**（157 卡的开源分类学：opening/effects/transition/outro...）
- 视觉语言从产品图主色生长（不另造皮肤）
- SFX 语义分类库（camera/impact/light/riser... 十六类）
- 人工确认环节：分镜表先给用户过目再渲染

### 线 3：视频复刻（方向明确，设计先行）
优秀视频 → 拆解（抽帧转图片路径 + 音频转写文本路径，视频理解兜底）→
结构模板（镜头表/节奏/风格 spec）→ 套自己的素材重渲染。本轮先落
「复刻拆解器」的输入输出契约，实现分阶段。

### 人工确认环节（横切所有产品线，本轮落地）
- 生成流程改为：规划产物（分镜表/匹配结果/风格）→ **staging 确认页** → 用户
  确认/修改 → 渲染。借 shotcraft「共同创作」模式的确认点设计：产品简报→
  视觉方向→镜头映射→分镜，每轮只问 1-3 个最能减少返工的问题。
- Checkpoint 已支持「确认后不重算上游」，天然契合。

## 五、shotcraft 借鉴结论（Apache-2.0 + Mixkit Free License，可放心借）

| 借鉴物 | 方式 |
|---|---|
| 镜头配方卡分类学（10 类 157 卡） | 结构思想 + 我们自建卡（参数表/已知坑/时长能量标签） |
| SFX 16 语义分类 + 台账纪律 | 直接采纳分类学；资产路径引用 shotcraft（Mixkit 免署名可商用），ATTRIBUTION 照抄纪律 |
| 共同创作确认点设计 | 采纳「每轮只问 1-3 个最减少返工的问题」 |
| 视觉语言从产品生长 | 采纳（产品图主色→配色 token） |
| Remotion/Workbench | 不引（Node 重型依赖），用 FFmpeg 等价实现 |

## 六、本轮落地项（并行，一次性交付）

- **A：LLM Provider 层（标配）**——OpenAI 兼容接口 Provider + 无 key 诚实降级
  到规则路径；文案优化/分镜增强/复刻拆解三个消费点接线
- **B：人工确认环节**——compose 流程插 staging 确认步骤（WebUI 确认页 +
  CLI --yes/--confirm），分镜表/匹配结果可视化给用户改
- **C：图片智能编辑线（线 1）**——image_analysis.py（构图/主色/显著性，
  纯 Python 替代方案）+ 图片动画渲染增强（多 motion 变体）
- **D：产品广告片线（线 2）**——shot 卡注册表（先 10 张核心卡）+
  product_promo pipeline + 产品图主色→风格映射

## 七、验收闭环

1. 各项 pytest 通过
2. LLM Provider：有 key 时文案优化真实调用（如你提供 key）；无 key 降级路径全绿
3. 人工确认：WebUI 确认页前端真实测试（Playwright：分镜表展示→修改→确认→渲染）
4. 图片线：真实图片 + 文案 → 动画视频 QC PASS
5. 产品线：产品图 + 简单文案 → 广告片 QC PASS
6. main 合并，git 干净