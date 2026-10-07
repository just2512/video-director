"""对抗性前端扫描：同面板内重复主按钮 / 重复 id / 重复文案直调按钮。

2026-10-07 用户实测：新建面板出现两个「AI 开始创作」按钮（cf-ai-go + cf-go），
此前的正面断言（按钮存在）全部通过——存在性测试天然查不出「多余」。本文件
从「多余」角度反向扫描：同一 onclick 处理器以相同文案渲染多个按钮即 FAIL。
"""

import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "webui" / "static" / "js"


def _panel_buttons(src: str):
    """yield (onclick, label, id) for <button ... onclick="fn()">label</button>."""
    for m in re.finditer(
        r'<button\b([^>]*?)>\s*([^<]{1,24}?)\s*</button>', src
    ):
        attrs, label = m.group(1), m.group(2).strip()
        oc = re.search(r'onclick="([^"]+)"', attrs)
        if not oc:
            continue
        bid = re.search(r'id="([^"]+)"', attrs)
        yield oc.group(1), label, (bid.group(1) if bid else "")


#: 多 modal/多列表区合法复用的「弱动作」按钮（取消/关闭/查看类）
_WEAK_LABELS = ("取消", "关闭", "查看", "刷新")


def test_no_duplicate_primary_submit_buttons_across_panels():
    dup = defaultdict(list)
    for f in sorted(ROOT.glob("*.js")):
        src = f.read_text(encoding="utf-8")
        for onclick, label, bid in _panel_buttons(src):
            # 忽略参数化调用（动态 pid）与弱动作复用；只盯「主提交」类文案
            if "$" in onclick or "{" in onclick:
                continue
            if any(w in label for w in _WEAK_LABELS):
                continue
            dup[(f.name, onclick, label)].append(bid or "(no-id)")
    offenders = {k: v for k, v in dup.items() if len(v) > 1}
    assert not offenders, (
        "同文件内相同 onclick+文案渲染了多个按钮（重复控件）：\n"
        + "\n".join(f"{k} → {v}" for k, v in offenders.items())
    )


#: 互斥渲染模板的合法同名 id（显式豁免清单，可审计）。
#: inspector.js 的场景上下文 / 图层上下文 / 镜头关系上下文三种模板先后
#: box.innerHTML 覆盖同一容器，同名 id 运行时永不共存（2026-09 既有模式）。
MUTEX_TEMPLATE_IDS = {
    "inspector.js": {
        "editor-picker-box",  # 场景模板 vs 图层模板，覆盖 #editor-inspector
        "ed-cont-type",       # 场景连续性 section vs 镜头关系上下文
        "ed-morph-row",
        "ed-cont-verify",
    },
}


def test_no_duplicate_element_ids_in_panel_html():
    """静态 id（不含 ${ 插值）在同文件重复才算缺陷。

    豁免：循环插值 id（mc-${role}-key，运行时每项唯一）与互斥渲染模板
    （MUTEX_TEMPLATE_IDS 显式清单）。打击面是「同一可见面板出现两个
    #cf-script」这类用户可感知的重复（2026-10-07 cf-ai-go/cf-go 双主按钮事故）。
    """
    offenders = []
    for f in sorted(ROOT.glob("*.js")):
        src = f.read_text(encoding="utf-8")
        exempt = MUTEX_TEMPLATE_IDS.get(f.name, set())
        ids = [i for i in re.findall(r'id="([^"]+)"', src)
               if "${" not in i and i not in exempt]
        seen = defaultdict(int)
        for i in ids:
            seen[i] += 1
        for i, n in seen.items():
            if n > 1:
                offenders.append(f'{f.name}: id="{i}" ×{n}')
    idx = (Path(__file__).resolve().parents[1] / "webui" / "static" / "index.html").read_text(encoding="utf-8")
    seen = defaultdict(int)
    for i in re.findall(r'id="([^"]+)"', idx):
        seen[i] += 1
    for i, n in seen.items():
        if n > 1:
            offenders.append(f'index.html: id="{i}" ×{n}')
    assert not offenders, "重复 DOM id：\n" + "\n".join(offenders)


def test_no_dead_button_ids_in_js():
    """面板里渲染的 button id 必须有 JS 引用（无引用=死控件或孤儿状态）。"""
    offenders = []
    for f in sorted(ROOT.glob("*.js")):
        src = f.read_text(encoding="utf-8")
        for bid in set(re.findall(r'<button[^>]*\bid="([^"]+)"', src)):
            # 渲染源文件自身引用，或任一 JS 文件引用
            ref = re.search(re.escape(bid), src)
            if ref:
                continue
            all_js = "\n".join(g.read_text(encoding="utf-8") for g in ROOT.glob("*.js"))
            if bid not in all_js:
                offenders.append(f"{f.name}: button id=\"{bid}\" 无任何引用")
    assert not offenders, "无引用按钮 id：\n" + "\n".join(offenders)