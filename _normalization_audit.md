# 系统能力归一 查重审计 + 落地记录（2026-09-29）

方案：`D:\openworkplace\content-wiki\系统能力归一.md`（59 节）。核心=能力归一：六层架构、三抽象（Capability/Provider/Artifact）、Composition IR 唯一事实源、单一 Creative Workspace、Adapter 防污染。

## 一、后端查重结论（死规则：能力存在≠方案落地，逐字段/枚举/消费点比对）

| 模块 | 行数 | 消费点 | 判定 |
|---|---|---|---|
| `capability_registry/` | 1427 | director/tool_executor/preview_service/server `/api/director/capabilities` | **方案§十二 Capability Registry 正解**（76 能力 id）保留 |
| `capabilities.py` | 617 | image_editor/__main__/server `/api/capabilities`（能力徽章+面板） | **非重复**：Provider 运行时探测（§五「Capability ≠ Provider」分层），保留 |
| `registries/` | 1920 | compiler/resolver/recipes/templates | **方案§十一 Recipe Registry 正解**，保留 |
| `registry.py` | 57 | planner（prompt 提示）/validator（IR 校验）/renderer（渲染） | **非死代码**：BasicRenderer 转场/motion 白名单，与 registries/ 是两层 Runtime，保留 |
| 顶层 `_test_*.py` ×7 + `_import_*.py` ×1 | ~72KB | 无（手动运行，已被 tests/ + tools/ 验收替代） | **死代码 → 已删除** |
| 顶层 `_test_*.py` ×3（未追踪） | ~37KB | 无 | **死代码 → 已删除** |

**结论**：后端无「重复功能」，只有 11 个死代码脚本。`capabilities.py` vs `capability_registry/`、`registry.py` vs `registries/` 均为方案§五定义的多层架构（Capability≠Provider、Recipe Registry≠Runtime 白名单），非功能重复。

## 二、前端查重结论（15 tab）

- **核心 Workspace**：`editor`（三栏 Library/Preview/Inspector + 十轨 + AI 指令栏）
- **与 editor 重复但已归位**：`motions`/`templates` 的「入口级」功能已补全进 editor Library（见三）；独立 tab 保留为「库管理页」（收藏/筛选/冻结台账等深功能，editor 快速换入口不含）
- **独立必要**：home/create/projects/assets(台账)/series/capture/reference/memory/review/publish/automation/settings

## 三、前端「假完成」bug 修复（3 个，指纹级发现的真问题）

1. **`edLibLoad()` 用了未定义变量 `kind`**（应为 `ED_LIB.nav`）→ 点击 Library 八入口抛 ReferenceError，列表永远空白。布局测试只断言按钮存在，不断言点击后可用，故漏检。→ 已修：9 处 `kind`→`ED_LIB.nav`。
2. **§46 Preview 用 `/files/` 前缀**加载草稿，但后端路由是 `/file?path=` → 预览视频 404 黑屏（假完成）。→ 已修：改 `/file?path=...`。
3. **editor Library templates 入口是「去模板库页试用」空壳指针**，motions 入口缺三区/预览 → 已补全：templates 入口就地试用（edLibOpenTemplate 复用 instantiateTemplate），motions 入口三区（入场/强调/出场）+ CSS 预览 + 应用。

## 四、前端显示修复（100+ 处英文/机械语言/§编号/字段名 → 中文）

- 顶栏五按钮：Undo/Redo/Save/Preview/Render → 撤销/重做/保存/预览/渲染
- Library 八入口：Scenes/Script/Assets/Stock/Generate/Motions/Templates/References → 场景/文案/素材/图库/生成/动效/模板/引用
- 十轨名：Narration/Dialogue/Original Audio/Main Visual/Graphic/Watermark/Subtitle/BGM/SFX → 旁白/对白/原声/主视觉/图形/水印/字幕/背景音乐/音效
- Inspector 四区块：Content/Layout/Style/Motion → 内容/布局/样式/动效；Context: Scene → 上下文：场景
- 字幕面板 11 标签：Mode/Font/Size/Color/Outline/W/Pos/Anim/Bg/Align → 模式/字体/字号/颜色/描边/描边宽/位置/动画/底色/对齐
- 分镜卡：Narration/Visual Intent/Visual Form/Block/Motion/Source → 旁白/视觉意图/视觉形态/视觉块/动效/来源
- 动效三区：Entrance/Emphasis/Exit → 入场/强调/出场
- §编号 20+ 处删除；字段名（motion_type/source/hash/commercialStatus/provider_id/input_fingerprint）→ 中文映射（新增 LAY_TYPE_CN/MOTION_TYPE_CN/COMMERCIAL_CN 三类映射函数）
- 其他：Media Library/Graphic Library/Style DNA/Preflight/Capability Map/Asset Inspector → 素材库/图形库/风格记忆/商用预检/能力地图/素材信息

## 五、验证证据（main 检出区 8501 + worktree 8533，零截图）

- Playwright 冒烟：无 JS 语法错误；顶栏/八入口/十轨全中文；Library motions 入口点击后渲染三区+应用，无新 pageerror
- 唯一残留 404 = favicon（无 favicon 链接，浏览器默认请求，预期）+ 无草稿时预览资源（预期）