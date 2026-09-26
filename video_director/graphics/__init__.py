"""Graphics Runtime / Creative Program 生产链（HyperFrames 集成：GFX-001~030 能力线）。

架构定位（方案 §3/§4/§60/§84）：
    Creative Intent → Graphic Plan → Block Resolve → GraphicProgram
        → HTML Compiler → 确定性 HTML/CSS（data-* 契约）
        → Browser Frame Capture（Playwright Chromium）→ FFmpeg → MP4

三条铁律（方案 §7/§48-50/§55）：
    1. Frame 是唯一时间事实：fps 有理化，seekFrame(frame) 驱动一切，
       禁止 Date.now()/performance.now()/Math.random()/网络响应决定画面。
    2. 沙箱隔离：生成 HTML 运行在隔离 origin + 禁网络，不可访问主 App DOM/cookie。
    3. Preview / Final 同一 Runtime：预览 iframe 与渲染 Chromium 共享同一份
       HTML + 同一套 CSS keyframes（parity by construction），无"预览是 CSS、
       成片是 FFmpeg"的漂移。

商业安全（方案 §45/§88/§末）：Block 全部自撰 HTML/CSS，origin=own/CLEAR；
不依赖 GSAP/Lottie/Three.js/Remotion——纯 CSS @keyframes + 负 delay 逐帧 scrub，
这是确定性可复现的近零依赖实现。

子模块：
    contract   Graphics Composition Contract（data-* 属性 + 有理 fps）
    program    GraphicProgram / GraphicScene 数据契约
    blocks     GraphicBlock 定义 + Creative Catalog（自撰块库）
    resolver   Block Resolver（SUPPORTED/MISSING_REQUIREMENT/INCOMPATIBLE，零评分）
    compiler   GraphicProgram → 确定性 HTML（含 FrameAdapter 注入 runtime）
    preflight  确定性预检（Date.now/random/fetch/外链字体 → REJECT）
    sandbox    隔离 origin + 网络阻断策略
    render     Browser Frame Capture（Playwright）+ FFmpeg 编码
    director   文案/数据 → GraphicPlan → GraphicProgram（Text→Graphic）
"""