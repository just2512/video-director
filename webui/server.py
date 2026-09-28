"""AI Video Director WebUI Server（Python 标准库 http.server，零第三方前端框架）。

替代 Streamlit：自己实现的单页 WebUI。
- GET  /            → 静态前端页面（webui/static/index.html）
- POST /api/compose  → 发起成片（multipart: script/videos/images/参数），同步阻塞返回 JSON
- GET  /api/voices   → 配音列表
- GET  /api/bgm      → BGM 列表（音频库扫描）
- GET  /api/health   → 健康检查
- GET  /file?path=   → 播放/下载产物（限项目目录内，防目录穿越）

用法：python run_webui.py（启动器自动调本模块）
"""

from __future__ import annotations

import json
import mimetypes
import re
import shutil
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from video_director import audio as vd_audio  # noqa: E402
from video_director import capabilities as vd_caps  # noqa: E402
from video_director import config as vd_config  # noqa: E402
from video_director import llm_provider as vd_llm  # noqa: E402
from video_director import model_config as vd_model_config  # noqa: E402
from video_director import pipeline  # noqa: E402
from video_director import prober as vd_prober  # noqa: E402
from video_director import store as vd_store  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"
STORAGE = ROOT / "storage"
LLM_CONFIG_FILE = STORAGE / "composer" / "llm_config.json"

# §三十四：通用 Registry 类别（shots/motions/templates/provenance 之外的 8 类）。
# route → kind（registry item kind 字段值）；kind → 子目录名（LayeredRegistryLoader）。
GENERIC_REGISTRY_KINDS = {
    "/api/registry/transitions": "transition",
    "/api/registry/styles": "style",
    "/api/registry/subtitles": "subtitle_style",
    "/api/registry/titles": "title_style",
    "/api/registry/lower_thirds": "lower_third",
    "/api/registry/graphics": "graphic",
    "/api/registry/audio": "audio_asset",
    "/api/registry/generators": "generator",
}
GENERIC_REGISTRY_SUBDIR = {
    "transition": "transitions",
    "style": "styles",
    "subtitle_style": "subtitles",
    "title_style": "titles",
    "lower_third": "lower_thirds",
    "graphic": "graphics",
    "audio_asset": "audio",
    "generator": "generators",
}

# 配音（中文 + 英文，EdgeTTS 免费零 key）。
# 白名单：经 edge-tts 实时合成验证（2026-09-24）——微软下架/脱节的音色
# （Xiaohan/Xiaomeng/Xiaorui/Yunfeng/Yunhao/东北话/陕西话/en-GB 族等）已移除，
# 保证前端每个音色都能真实试听+配音。微软再调整时需重新验证（tools 有验证脚本）。
VOICES = [
    ("zh-CN-XiaoxiaoNeural", "晓晓（女·温柔）"),
    ("zh-CN-XiaoyiNeural", "晓伊（女·活泼）"),
    ("zh-CN-YunjianNeural", "云健（男·沉稳）"),
    ("zh-CN-YunxiNeural", "云希（男·阳光）"),
    ("zh-CN-YunxiaNeural", "云夏（男·清亮）"),
    ("zh-CN-YunyangNeural", "云扬（男·新闻）"),
    ("zh-TW-HsiaoChenNeural", "曉臻（台湾·女）"),
    ("zh-TW-YunJheNeural", "雲哲（台湾·男）"),
    ("zh-TW-HsiaoYuNeural", "曉雨（台湾·女）"),
    ("en-US-EmmaNeural", "Emma (US Female)"),
    ("en-US-AndrewNeural", "Andrew (US Male)"),
    ("en-US-BrianNeural", "Brian (US Male)"),
    ("en-US-AvaMultilingualNeural", "Ava (Multilingual)"),
    ("en-US-BrianMultilingualNeural", "Brian (Multilingual)"),
    ("en-US-AndrewMultilingualNeural", "Andrew (Multilingual)"),
]

# 画幅
ASPECTS = [
    ("1080x1920", "9:16 竖屏（抖音/快手）"),
    ("1920x1080", "16:9 横屏（B站/YouTube）"),
    ("1080x1080", "1:1 方形（小红书/朋友圈）"),
    ("1080x1440", "3:4 竖屏（小红书图文）"),
    ("1440x1080", "4:3 横屏（传统）"),
    ("1920x822", "21:9 电影宽屏"),
]


# 音频中文标签（shotcraft Mixkit 库 16 类 + BGM 曲目；前端下拉直接显示中文）
AUDIO_LABEL_CN = {
    # SFX 16 类
    "camera": "相机快门", "counter": "钟表滴答", "crowd": "人群欢呼", "data": "数据科技",
    "film": "胶片放映", "fluid": "水滴气泡", "glass": "玻璃破碎", "impact": "冲击重音",
    "light": "光效魔法", "mech": "机械金属", "paper": "纸张翻动", "riser": "上升音效",
    "scifi": "科幻电子", "text": "书写打字", "transition": "转场呼啸", "ui": "界面交互",
    # BGM 曲目
    "bgm-tech-house": "科技浩室（电子节奏）", "cat-walk": "猫步（优雅律动）",
    "g-eazy-nba-type": "街头篮球（嘻哈能量）", "house-vibez": "浩室氛围（轻快律动）",
    "tonight-hiphop": "今夜嘻哈（低音节拍）", "ambient-pad-001": "环境音垫（舒缓铺底）",
    # 风格合成 BGM（generate_style_bgm_library）
    "bgm-calm-piano-pad": "舒缓钢琴垫（纪录片/情感）",
    "bgm-upbeat-trend": "轻快流行（短视频/种草）",
    "bgm-epic-tension": "史诗紧张（历史/悬疑）",
    "bgm-tech-neutral": "科技中性（产品/解说）",
    "bgm-warm-story": "温暖叙事（人物/故事）",
}


def _audio_label(asset_id: str, tags: list = None) -> str:
    """音频中文名：映射表 > tags > id。"""
    if asset_id in AUDIO_LABEL_CN:
        return AUDIO_LABEL_CN[asset_id]
    for t in (tags or []):
        if t in AUDIO_LABEL_CN:
            return AUDIO_LABEL_CN[t]
    # sfx-camera-camera-lens-shutter → 相机快门·镜头快门声
    parts = asset_id.split("-")
    if len(parts) >= 2 and parts[0] == "sfx" and parts[1] in AUDIO_LABEL_CN:
        sub = "-".join(parts[2:]).replace("-", " ")
        return f"{AUDIO_LABEL_CN[parts[1]]}·{sub}"
    # 用户上传：bgm-upload-<文件名> / sfx-upload-<文件名>
    if "-upload-" in asset_id:
        kind = "背景音乐" if asset_id.startswith("bgm") else "音效"
        return f"我的{kind}·{'-'.join(asset_id.split('-')[2:])}"
    # bgm-<名>：查无映射时用去前缀名
    if asset_id.startswith("bgm-"):
        return asset_id[4:].replace("-", " ")
    return asset_id


def _scan_bgm() -> list:
    try:
        lib = vd_audio.scan_library()
        return [{"id": a.id, "label": _audio_label(a.id, a.keywords)}
                for a in lib.values() if a.type == "bgm"]
    except Exception:
        return []


def _scan_sfx() -> list:
    try:
        lib = vd_audio.scan_library()
        return [{"id": a.id, "label": _audio_label(a.id, a.keywords)}
                for a in lib.values() if a.type == "sfx"]
    except Exception:
        return []


def _load_llm_config() -> None:
    """启动时恢复模型配置：model_config.json（新中心）+ 旧 llm_config.json 迁移。"""
    try:
        # 旧 llm_config.json 存在且新中心未配置 llm 角色 → 迁移
        if LLM_CONFIG_FILE.is_file():
            try:
                old = json.loads(LLM_CONFIG_FILE.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                old = {}
            if old.get("api_key") and not vd_model_config.get_role_config("llm").get("api_key"):
                vd_model_config.save_all({"llm": {
                    "api_key": old.get("api_key", ""),
                    "base_url": old.get("base_url", ""),
                    "model": old.get("model", ""),
                }})
                print("[web] 旧 llm_config.json 已迁移到 model_config.json", file=sys.stderr)
        vd_model_config.sync_llm_provider()
    except Exception as exc:
        print(f"[web] 加载模型配置失败: {exc}", file=sys.stderr)


def _read_project_draft(project_id: str) -> dict:
    """从项目 plans/ 读初始方案字段（draft 源，给偏好 diff 用）。

    优先级：storyboard.json（preview 分镜书）> style.json+scenes.json（direct
    也落盘）。返回 {"style","scene_count","total_duration"}；读不到给零占位。
    """
    plans = vd_config.project_dir(project_id) / "plans"
    draft = {"style": "", "scene_count": 0, "total_duration": 0.0}
    try:
        sb_file = plans / "storyboard.json"
        if sb_file.is_file():
            sb = json.loads(sb_file.read_text(encoding="utf-8"))
            if isinstance(sb, dict):
                draft["style"] = sb.get("style_id", "") or draft["style"]
                draft["scene_count"] = sb.get(
                    "scene_count", len(sb.get("scenes", []) or []))
                draft["total_duration"] = round(
                    float(sb.get("total_duration", 0) or 0), 2)
                return draft
        style_file = plans / "style.json"
        if style_file.is_file():
            st = json.loads(style_file.read_text(encoding="utf-8"))
            if isinstance(st, dict):
                draft["style"] = st.get("style_id", "") or draft["style"]
        scenes_file = plans / "scenes.json"
        if scenes_file.is_file():
            sc = json.loads(scenes_file.read_text(encoding="utf-8"))
            if isinstance(sc, list):
                draft["scene_count"] = len(sc)
            elif isinstance(sc, dict):
                draft["scene_count"] = len(sc.get("scenes", []) or [])
    except Exception as exc:
        print(f"[web] _read_project_draft({project_id}) 失败: {exc}",
              file=sys.stderr)
    return draft


def _parse_multipart(handler) -> dict:
    """解析 multipart/form-data（不引第三方库，手写解析器）。

    返回 {fields: {name: str}, files: [{name, filename, data}]}。
    大 body 必须循环读到 Content-Length 为止（rfile.read(n) 单次可能读不满）。
    """
    ctype = handler.headers.get("Content-Type", "")
    m = re.search(r"boundary=(.+)", ctype)
    if not m:
        return {"fields": {}, "files": []}
    boundary = m.group(1).strip().strip('"').encode()
    length = int(handler.headers.get("Content-Length", 0))
    # 循环读满：网络分片到达时单次 read 不足 length
    chunks = []
    remaining = length
    while remaining > 0:
        chunk = handler.rfile.read(min(remaining, 65536))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    body = b"".join(chunks)
    parts = body.split(b"--" + boundary)
    fields, files = {}, []
    for part in parts:
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        if b"\r\n\r\n" not in part:
            continue
        header_blob, data = part.split(b"\r\n\r\n", 1)
        header_text = header_blob.decode("utf-8", errors="replace")
        name_m = re.search(r'name="([^"]+)"', header_text)
        file_m = re.search(r'filename="([^"]*)"', header_text)
        if not name_m:
            continue
        name = name_m.group(1)
        if file_m and file_m.group(1):
            files.append({"name": name, "filename": file_m.group(1), "data": data})
        else:
            fields[name] = data.decode("utf-8", errors="replace")
    return {"fields": fields, "files": files}


class Handler(BaseHTTPRequestHandler):
    server_version = "AI-Video-Director/1.0"

    def log_message(self, fmt, *args):
        print(f"[web] {self.address_string()} {fmt % args}", file=sys.stderr)

    def _send_bytes(self, data: bytes, ctype: str, status=200):
        """发送响应；客户端中途断开时静默返回，避免打满屏 traceback。"""
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        try:
            self.end_headers()
            self.wfile.write(data)
        except ConnectionError:
            pass

    def _send_json(self, obj, status=200):
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send_bytes(payload, "application/json; charset=utf-8", status)

    def _send_html(self, path: Path, status=200):
        data = path.read_bytes()
        ctype, _ = mimetypes.guess_type(str(path))
        self._send_bytes(data, ctype or "text/html; charset=utf-8", status)

    # ------------------------------------------------------------------
    def _registry_motions(self, parsed):
        """GET /api/registry/motions：动效清单（Motion Gallery 数据源）。

        附 channels 摘要（前端 CSS 循环预览的 keyframes 来源）。
        """
        try:
            from video_director.registries.recipes import MotionRegistry
            out = []
            for m in MotionRegistry().all():
                out.append({
                    "id": m.id, "version": m.version, "name": m.name,
                    "motion_type": m.motion_type,
                    "semantic_intents": list(m.semantic_intents),
                    "compatible_layers": list(m.compatible_layers),
                    "duration_recommended": m.duration_recommended,
                    "duration_range": [m.duration_min, m.duration_max],
                    "channels": [
                        {"property": c.property,
                         "keyframes": [{"at": k.at, "value": k.value} for k in c.keyframes]}
                        for c in m.channels
                    ],
                    "parameters": list(m.parameters),
                    "preview_only": "renderer_preview_only" in m.constraints,
                    "commercial_status": m.commercial_status,
                })
            self._send_json(out)
        except Exception as exc:
            self._send_json({"error": str(exc)}, 500)

    def _registry_templates(self, parsed):
        """GET /api/registry/templates：模板清单（Template Gallery 数据源）。"""
        try:
            from video_director.registries.templates import TemplateRegistry
            qs = parse_qs(parsed.query)
            category = qs.get("category", [""])[0]
            ratio = qs.get("ratio", [""])[0]
            out = []
            for t in TemplateRegistry().all():
                if category and t.category != category:
                    continue
                if ratio and ratio not in t.supported_ratios:
                    continue
                out.append({
                    "id": t.id, "version": t.version, "name": t.name,
                    "category": t.category,
                    "supported_ratios": list(t.supported_ratios),
                    "duration_recommended": t.duration_recommended,
                    "slots": [
                        {"id": sl.id, "label": sl.label, "type": sl.type,
                         "required": sl.required}
                        for sl in t.slots
                    ],
                    "scene_count": len(t.scenes),
                    "commercial_status": t.commercial_status,
                })
            self._send_json(out)
        except Exception as exc:
            self._send_json({"error": str(exc)}, 500)

    def _generic_registry(self, parsed, kind: str):
        """GET /api/registry/{transitions|styles|...}：§三十四 通用 Registry 清单。"""
        try:
            from video_director.registries.core import LayeredRegistryLoader
            sub = GENERIC_REGISTRY_SUBDIR[kind]
            loader = LayeredRegistryLoader(kind, sub)
            out = [{
                "id": it.id, "version": it.version, "name": it.name,
                "kind": it.kind, "tags": list(it.tags),
                "description": it.description,
                "commercial_status": it.commercial_status,
                "provenance_ref": it.provenance_ref,
                "source_layer": loader.layer_of(it.id, it.version),
            } for it in loader.load_all()]
            self._send_json(out)
        except Exception as exc:
            self._send_json({"error": str(exc)}, 500)

    def _style_dna(self, parsed):
        """GET /api/style_dna：明确行为台账（§三十二，零评分）+ 每项行为计数。"""
        try:
            from video_director import memory as vd_memory
            behaviors = vd_memory.list_motion_behaviors()
            summary = vd_memory.motion_behavior_summary()
            self._send_json({"behaviors": behaviors, "summary": summary})
        except Exception as exc:
            self._send_json({"error": str(exc)}, 500)

    def _asset_detail(self, parsed):
        """GET /api/asset_detail?asset_id=&project_id=：（EDT-005 AssetInspector）"""
        try:
            qs = parse_qs(parsed.query)
            asset_id = qs.get("asset_id", [""])[0]
            pid = qs.get("project_id", [""])[0]
            # 全库索引（source/used_in）+ 项目 manifest（duration/fps/license）
            from pathlib import Path as _P
            root = STORAGE / "composer"
            source = "user"
            license_note = None
            used_in = []
            duration = None
            width = height = None
            atype = None
            prompt = None
            for d in root.iterdir() if root.is_dir() else []:
                man = d / "assets" / "manifest.json"
                if not man.is_file():
                    continue
                try:
                    entries = json.loads(man.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                for a in entries:
                    if a.get("id") == asset_id:
                        if a.get("source"):
                            source = a["source"]
                        if a.get("license_note"):
                            license_note = a["license_note"]
                        if d.name not in used_in:
                            used_in.append(d.name)
                        duration = a.get("duration")
                        width = a.get("width")
                        height = a.get("height")
                        atype = a.get("type")
                        prompt = a.get("prompt")
            from video_director import preflight as vd_preflight
            status = vd_preflight.commercial_status_of_origin(source)
            self._send_json({
                "asset_id": asset_id, "source": source, "type": atype,
                "license_note": license_note, "commercial_status": status,
                "used_in": used_in, "duration": duration,
                "width": width, "height": height, "prompt": prompt,
            })
        except Exception as exc:
            self._send_json({"error": str(exc)}, 500)

    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path

        if route == "/" or route == "/index.html":
            self._send_html(STATIC_DIR / "index.html")
        elif route == "/api/health":
            self._send_json({"ok": True, "app": "AI Video Director"})
        elif route == "/api/voices":
            self._send_json([{"id": v, "label": l} for v, l in VOICES])
        elif route == "/api/voice/providers":
            # §13/§14：TTSProvider Registry + VoiceProfile（Audio UI 音色试听）
            try:
                from video_director import voice as vd_voice
                self._send_json({"providers": vd_voice.list_providers(),
                                 "voices": vd_voice.list_voices()})
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
        elif route == "/api/aspects":
            self._send_json([{"value": a, "label": l} for a, l in ASPECTS])
        elif route == "/api/bgm":
            self._send_json(_scan_bgm())
        elif route == "/api/sfx":
            self._send_json(_scan_sfx())
        elif route == "/api/llm_status":
            self._send_json(vd_llm.get_llm_config())
        elif route == "/api/model_config":
            # 多模型配置中心状态（四角色：llm/image/video/vision）
            try:
                self._send_json(vd_model_config.status_for_frontend())
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
        elif route == "/api/audio_preview":
            # 音频库试听：?asset_id=bgb-xxx → 音频文件流（限音频库内，防穿越）
            qs = parse_qs(parsed.query)
            asset_id = qs.get("asset_id", [""])[0]
            try:
                lib = vd_audio.scan_library()
                a = lib.get(asset_id)
                if a is None:
                    self._send_json({"error": f"音频库无 {asset_id}"}, 404)
                    return
                data = Path(a.file).read_bytes()
                self._send_bytes(data, "audio/mpeg")
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
        elif route == "/api/voice_preview":
            # 配音试听：?voice=zh-CN-XiaoxiaoNeural → edge-tts 合成固定短句（缓存）
            qs = parse_qs(parsed.query)
            voice = qs.get("voice", [""])[0]
            if not voice:
                self._send_json({"error": "缺少 voice"}, 400)
                return
            cache_dir = STORAGE / "composer" / "voice_previews"
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = cache_dir / f"{voice}.mp3"
            try:
                # 缓存命中条件：存在且非空（0 字节 = 上次合成失败的残留，重合成）
                if not (cache_file.is_file() and cache_file.stat().st_size > 0):
                    import asyncio
                    import edge_tts
                    async def _syn():
                        com = edge_tts.Communicate("这是一段配音试听，检查音色是否合适。", voice)
                        await com.save(str(cache_file))
                    # edge-tts 偶发截断（限流/网络）：失败清理空文件，最多重试 2 次
                    last_err = None
                    for _ in range(3):
                        try:
                            asyncio.run(_syn())
                            if cache_file.stat().st_size > 0:
                                break
                            cache_file.unlink(missing_ok=True)
                        except Exception as e1:
                            last_err = e1
                            cache_file.unlink(missing_ok=True)
                    else:
                        raise RuntimeError(f"合成多次失败（{last_err}）")
                data = cache_file.read_bytes()
                if not data:
                    raise RuntimeError("合成产物为空")
                self._send_bytes(data, "audio/mpeg")
            except Exception as exc:
                # 失败响应带明确状态码，前端能区分"音色不可用"和"网络失败"
                try:
                    cache_file.unlink(missing_ok=True)
                except Exception:
                    pass
                self._send_json({"error": f"试听合成失败: {exc}"}, 502)
            return
        elif route == "/api/doctor":
            # Settings 页环境自检（ffmpeg/font/存储/渲染器）
            try:
                from video_director import doctor as vd_doctor
                results = vd_doctor.run_doctor()
                self._send_json([{
                    "name": r.name, "status": r.status, "detail": r.detail,
                } for r in results])
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
        elif route == "/api/capabilities":
            try:
                caps = vd_caps.registry()
                data = []
                for name, cap in caps.items():
                    data.append({
                        "name": name,
                        "status": cap.status,
                        "providers": [
                            {"name": p.name, "available": p.available,
                             "requires_api_key": p.requires_api_key}
                            for p in (cap.providers or [])
                        ],
                        "supports": list(cap.supports or []),
                        "fallback_tools": list(cap.fallback_tools or []),
                    })
                self._send_json(data)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
        elif route == "/api/style_dna":
            # 创作记忆视图：Style DNA + 最近偏好 + 历史案例数
            try:
                from video_director import memory as vd_memory
                vd_memory.init_db()
                dna = vd_memory.get_style_dna()
                prefs = vd_memory.list_preferences(limit=10)
                cases = vd_memory.list_cases(limit=50)
                liked_count = sum(1 for c in cases if c.get("feedback_liked") == 1)
                self._send_json({
                    "style_dna": dna,
                    "recent_preferences": prefs,
                    "case_count": len(cases),
                    "liked_count": liked_count,
                })
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
        elif route == "/api/projects":
            # Project Runtime：项目索引（Portable Project 列表 + 状态 + 可续渲）
            try:
                root = STORAGE / "composer"
                projects = []
                if root.is_dir():
                    for d in sorted(root.iterdir(),
                                    key=lambda p: p.stat().st_mtime, reverse=True):
                        if not (d.name.startswith("web-") or d.name.startswith("tpl-")
                                or d.name.startswith("gfx-")):
                            continue
                        run_json = d / "plans" / "run.json"
                        final = d / "plans" / "storyboard.json"
                        final_out = d / "output" / "final.mp4"
                        sb = d / "plans" / "storyboard.json"
                        draft = d / "output" / "draft.mp4"
                        meta = {"project_id": d.name,
                                "mtime": int(d.stat().st_mtime),
                                "has_final": final_out.is_file(),
                                "final_path": str(final_out) if final_out.is_file() else "",
                                "draft_exists": draft.is_file(),
                                "pending_confirm": (sb.is_file()
                                                     and not final_out.is_file())}
                        if run_json.is_file():
                            try:
                                rj = json.loads(run_json.read_text(encoding="utf-8"))
                                meta["providers"] = rj.get("providers", {})
                                meta["qc"] = (rj.get("verification", {})
                                              .get("deterministic_qc"))
                            except (OSError, json.JSONDecodeError):
                                pass
                        projects.append(meta)
                    projects = projects[:30]  # 首页最近 30
                self._send_json(projects)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
        elif route == "/api/project_detail":
            # 项目详情（重开续用：产物清单 + 分镜 + IR 概览）
            qs = parse_qs(parsed.query)
            pid = qs.get("project_id", [""])[0]
            if not pid:
                self._send_json({"error": "缺少 project_id"}, 400)
                return
            try:
                pdir = STORAGE / "composer" / pid
                if not pdir.is_dir():
                    self._send_json({"error": "项目不存在"}, 404)
                    return
                detail = {"project_id": pid}
                sb_file = pdir / "plans" / "storyboard.json"
                if sb_file.is_file():
                    sb = json.loads(sb_file.read_text(encoding="utf-8"))
                    detail["storyboard"] = {
                        "scene_count": sb.get("scene_count"),
                        "total_duration": sb.get("total_duration"),
                        "scenes": [
                            {"scene_id": s.get("scene_id"), "text": s.get("text"),
                             "duration": s.get("duration"),
                             "assets": [a.get("asset_id")
                                        for a in (s.get("assets_used") or [])]}
                            for s in sb.get("scenes", [])
                        ],
                    }
                comp = pdir / "plans" / "composition.json"
                if comp.is_file():
                    c = json.loads(comp.read_text(encoding="utf-8"))
                    detail["transitions"] = [
                        s.get("transition_out", {}).get("type")
                        for s in c.get("scenes", [])
                    ]
                final_p = pdir / "output" / "final.mp4"
                detail["final_exists"] = final_p.is_file()
                if final_p.is_file():
                    detail["final_path"] = str(final_p)
                detail["draft_exists"] = any(
                    (pdir / "render").glob("draft*.mp4"))
                script_file = pdir / "input" / "script.txt"
                if script_file.is_file():
                    detail["script"] = script_file.read_text(encoding="utf-8")[:2000]
                self._send_json(detail)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
        elif route == "/api/assets_library":
            # Asset Library：全项目素材索引 + 使用历史（Asset Graph UI）
            try:
                root = STORAGE / "composer"
                lib = {}
                if root.is_dir():
                    for d in root.iterdir():
                        man = d / "assets" / "manifest.json"
                        if not man.is_file():
                            continue
                        try:
                            entries = json.loads(man.read_text(encoding="utf-8"))
                        except (OSError, json.JSONDecodeError):
                            continue
                        for a in entries:
                            if a.get("type") not in ("video", "image"):
                                continue  # 音频库另走 /api/bgm /api/sfx
                            aid = a.get("id")
                            ent = lib.setdefault(aid, {
                                "asset_id": aid, "type": a.get("type"),
                                "file": a.get("file"), "duration": a.get("duration"),
                                "description": a.get("description"),
                                "source": a.get("source", "user"),
                                "used_in": [],
                            })
                            if a.get("description"):
                                ent["description"] = a["description"]
                            # source 取最新（越新的项目 source 越可信；缺省 user）
                            if a.get("source"):
                                ent["source"] = a["source"]
                            if d.name not in ent["used_in"]:
                                ent["used_in"].append(d.name)
                self._send_json(list(lib.values()))
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
        elif route == "/api/inbox":
            # Capture Inbox（§30 收集箱）
            try:
                from video_director import studio as vd_studio
                self._send_json(vd_studio.inbox_list())
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
        elif route == "/api/series":
            # 系列模板列表（§5）
            try:
                from video_director import studio as vd_studio
                self._send_json(vd_studio.series_list())
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
        elif route == "/api/jobs":
            # Automation 队列（§42：状态可查）
            try:
                from video_director import studio as vd_studio
                self._send_json(vd_studio.job_list())
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
        elif route == "/api/registry/motions":
            self._registry_motions(parsed)
        elif route == "/api/registry/templates":
            self._registry_templates(parsed)
        elif route in GENERIC_REGISTRY_KINDS:
            self._generic_registry(parsed, GENERIC_REGISTRY_KINDS[route])
        elif route == "/api/style_dna/behaviors":
            self._style_dna(parsed)
        elif route == "/api/asset_detail":
            self._asset_detail(parsed)
        elif route == "/api/system_identity":
            # §90 系统定位自描述 + §61 Capability Map（能力面板/首页消费）
            try:
                from video_director.graphics import visual_elements as vd_ve
                ident = vd_ve.system_identity()
                ident["capability_map"] = vd_ve.capability_map()
                self._send_json(ident)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
        elif route == "/api/media_library":
            # §44 Media Library：冻结资产台账（source/hash/commercialStatus/复用）
            try:
                from video_director.graphics import asset_resolver as vd_ar
                self._send_json(vd_ar._load_manifest())
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
        elif route == "/api/catalog/blocks":
            # §64 Graphic Blocks：GET /api/catalog/blocks（Block Library 数据源）
            try:
                from video_director.graphics import blocks as vd_gfx_blocks
                qs = parse_qs(parsed.query)
                block_id = qs.get("id", [""])[0]
                if block_id:
                    b = vd_gfx_blocks.get_block(block_id)
                    self._send_json({
                        "id": b.id, "name": b.name, "category": b.category,
                        "semantic_intents": list(b.semantic_intents),
                        "slots": list(b.slots),
                        "duration_range": [b.duration_min, b.duration_max],
                        "motion": b.motion,
                    })
                    return
                self._send_json([{
                    "id": b.id, "name": b.name, "category": b.category,
                    "semantic_intents": list(b.semantic_intents),
                    "slots": list(b.slots),
                    "duration_range": [b.duration_min, b.duration_max],
                    "motion": b.motion,
                } for b in vd_gfx_blocks.all_blocks()])
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
        elif route == "/api/catalog/blocks/resolve" or route == "/api/graphics/blocks/resolve":
            # GFX-010 Block Resolver（POST，见 do_POST）——GET 拒绝
            self._send_json({"error": "POST only"}, 405)
        elif route == "/api/editor/composition":
            # ?project_id= → 场景/层时间线数据（Editor 页事实源）
            qs = parse_qs(parsed.query)
            pid = qs.get("project_id", [""])[0]
            if not pid:
                self._send_json({"error": "缺少 project_id"}, 400)
                return
            try:
                from video_director import editor as vd_editor
                ir = vd_editor.load_composition(pid)
                revs = vd_editor.load_revisions(pid)
                self._send_json({
                    "project_id": pid,
                    "version": len(revs) + 1,
                    "width": ir.project.width, "height": ir.project.height,
                    "duration": ir.project.duration,
                    "origin_template_id": ir.origin_template_id,
                    "origin_template_version": ir.origin_template_version,
                    "scenes": [
                        {
                            "id": sc.id, "start": sc.start, "duration": sc.duration,
                            "text": sc.text,
                            "origin_recipe_id": sc.origin_recipe_id,
                            "layers": [
                                {
                                    "index": i, "type": lay.type.value,
                                    "asset_id": lay.asset_id, "start": lay.start,
                                    "duration": lay.duration,
                                    "motion_recipe_id": lay.motion_recipe_id,
                                    "motion_version": lay.motion_version,
                                    "locked": bool(getattr(lay, "locked", None) or False) or None,
                                } for i, lay in enumerate(sc.layers)
                            ],
                        } for sc in ir.scenes
                    ],
                    # §18/§19/§32：音频轨/水印/字幕样式回传（Inspector/时间线可视化）
                    "audio": [
                        {"type": t.type.value, "asset_id": t.asset_id, "start": t.start,
                         "duration": t.duration, "volume": t.volume, "ducking": t.ducking}
                        for t in ir.audio
                    ],
                    "watermarks": [
                        {"kind": w.kind, "text": w.text, "asset_id": w.asset_id,
                         "anchor": w.anchor, "opacity": w.opacity, "scale": w.scale}
                        for w in (ir.watermarks or [])
                    ],
                    "subtitle_style": ir.subtitle_style.model_dump() if ir.subtitle_style else None,
                    "revisions_count": len(revs),
                })
            except Exception as exc:
                self._send_json({"error": str(exc)}, 404)
        elif route == "/api/editor/revisions":
            # ?project_id= → revision 历史（Versions 页消费）
            qs = parse_qs(parsed.query)
            pid = qs.get("project_id", [""])[0]
            try:
                from video_director import editor as vd_editor
                self._send_json(vd_editor.load_revisions(pid))
            except Exception as exc:
                self._send_json({"error": str(exc)}, 404)
        elif route == "/api/preflight":
            # ?project_id= → 商用预检报告（发布前闸门）
            qs = parse_qs(parsed.query)
            pid = qs.get("project_id", [""])[0]
            if not pid:
                self._send_json({"error": "缺少 project_id"}, 400)
                return
            try:
                from video_director import preflight as vd_preflight
                report = vd_preflight.run_preflight(pid, for_publish=True)
                self._send_json(report.to_dict())
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
        elif route == "/api/director/capabilities":
            # §5/§63：编辑能力 Manifest（Level-1 摘要 / 按 id describe Level-2）
            try:
                from video_director.capability_registry import (
                    describe_capability, list_capabilities)
                qs = parse_qs(parsed.query)
                cap_id = qs.get("id", [""])[0]
                if cap_id:
                    self._send_json({"capability": describe_capability(cap_id)})
                    return
                self._send_json({"capabilities": list_capabilities()})
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
        elif route == "/api/director/context":
            # §6：Project Context 快照（Director 读当前状态用）
            qs = parse_qs(parsed.query)
            pid = qs.get("project_id", [""])[0]
            if not pid:
                self._send_json({"error": "缺少 project_id"}, 400)
                return
            try:
                from video_director import project_context
                self._send_json({"context": project_context.inspect_project_context(pid)})
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
        elif route == "/file":
            qs = parse_qs(parsed.query)
            fpath = qs.get("path", [""])[0]
            p = Path(fpath)
            # 安全：仅允许 storage 内文件
            try:
                p.resolve().relative_to(STORAGE.resolve())
            except ValueError:
                self._send_json({"error": "path out of storage"}, 403)
                return
            if p.is_file():
                data = p.read_bytes()
                ctype, _ = mimetypes.guess_type(str(p))
                self._send_bytes(data, ctype or "application/octet-stream")
            else:
                self._send_json({"error": "not found"}, 404)
        else:
            self._send_json({"error": "not found"}, 404)

    # ------------------------------------------------------------------
    def do_POST(self):
        parsed = urlparse(self.path)

        # 音频本地上传（参考 MPT BgmUpload 设计）：白名单扩展名 + 30MB 上限 +
        # ffprobe 音频流校验 + 安全文件名 → audio_library/uploads/ → 出现在下拉
        if parsed.path == "/api/inbox_add":
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON（需 UTF-8）"}, 400)
                return
            content = (payload.get("content") or "").strip()
            if not content:
                self._send_json({"error": "内容不能为空"}, 400)
                return
            try:
                from video_director import studio as vd_studio
                item_id = vd_studio.inbox_add(
                    payload.get("kind", "idea"), content,
                    (payload.get("note") or "").strip())
                self._send_json({"ok": True, "item_id": item_id})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        if parsed.path == "/api/inbox_action":
            # 收集箱条目操作：converted（转创作）/ dismissed（丢弃）/ delete
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON（需 UTF-8）"}, 400)
                return
            try:
                from video_director import studio as vd_studio
                item_id = payload.get("item_id", "")
                act = payload.get("action", "")
                if not item_id or not act:
                    self._send_json({"error": "缺少 item_id/action"}, 400)
                    return
                if act == "delete":
                    vd_studio.inbox_delete(item_id)
                else:
                    vd_studio.inbox_set_status(item_id, act)
                self._send_json({"ok": True})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        if parsed.path == "/api/series_create":
            # 系列模板（§5：默认配音/画幅/字幕/BGM/风格/水印）
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON（需 UTF-8）"}, 400)
                return
            name = (payload.get("name") or "").strip()
            if not name:
                self._send_json({"error": "系列名不能为空"}, 400)
                return
            try:
                from video_director import studio as vd_studio
                sid = vd_studio.series_create(name, payload.get("spec") or {})
                self._send_json({"ok": True, "series_id": sid})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        if parsed.path == "/api/series_batch":
            # 系列批量生产（§42：多集文案逐集入队）
            mp = _parse_multipart(self)
            fields, files = mp["fields"], mp["files"]
            series_id = fields.get("series_id", "")
            if not series_id:
                self._send_json({"error": "缺少 series_id"}, 400)
                return
            scripts_raw = fields.get("scripts", "")
            # 多集文案：以「---」或空行分隔
            episodes = []
            for i, chunk in enumerate(scripts_raw.split("------------")):
                txt = chunk.strip()
                if txt:
                    episodes.append({"episode": i + 1, "script": txt})
            if not episodes:
                self._send_json({"error": "请按「------------」分隔粘贴多集文案"}, 400)
                return
            try:
                from video_director import studio as vd_studio
                series = {s["series_id"]: s for s in vd_studio.series_list()}
                spec = series.get(series_id, {}).get("spec", {})
                vids = [f["filename"] for f in files if f["name"] == "videos"]
                # 素材落盘（系列共享素材目录）
                video_paths = []
                if vids:
                    shared_dir = STORAGE / "composer" / f"series-{series_id[:12]}" / "input" / "videos"
                    shared_dir.mkdir(parents=True, exist_ok=True)
                    for f in files:
                        if f["name"] != "videos":
                            continue
                        dst = shared_dir / Path(f["filename"]).name
                        dst.write_bytes(f["data"])
                        video_paths.append(str(dst))
                job_ids = vd_studio.enqueue_series_batch(
                    series_id, spec, episodes, video_paths)
                self._send_json({"ok": True, "job_ids": job_ids,
                                 "episodes": len(episodes)})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        if parsed.path == "/api/variants_batch":
            # 多平台变体批量（§41：项目 → 各平台画幅变体入队）
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON（需 UTF-8）"}, 400)
                return
            pid = payload.get("project_id", "")
            platforms = payload.get("platforms") or []
            if not pid or not platforms:
                self._send_json({"error": "缺少 project_id 或 platforms"}, 400)
                return
            try:
                from video_director import studio as vd_studio
                job_ids = vd_studio.enqueue_platform_variants(pid, platforms)
                self._send_json({"ok": True, "job_ids": job_ids})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        if parsed.path == "/api/audio_upload":
            mp = _parse_multipart(self)
            fields, files = mp["fields"], mp["files"]
            kind = fields.get("kind", "bgm")  # bgm | sfx
            up = [f for f in files if f["name"] == "audio_file"]
            if not up:
                self._send_json({"error": "请选择音频文件"}, 400)
                return
            f = up[0]
            fname = Path(f["filename"]).name
            ext = Path(fname).suffix.lower()
            ALLOWED = (".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma")
            MAX_BYTES = 30 * 1024 * 1024
            if ext not in ALLOWED:
                self._send_json(
                    {"error": f"不支持的格式 {ext}，允许：{'/'.join(ALLOWED)}"}, 400)
                return
            if len(f["data"]) > MAX_BYTES:
                self._send_json({"error": "文件超过 30MB 上限"}, 400)
                return
            try:
                lib_root = vd_audio.library_root()
                up_dir = lib_root / "uploads"
                up_dir.mkdir(parents=True, exist_ok=True)
                # 安全文件名：去 Windows 非法字符，重名自动加序号
                stem = "".join(c for c in Path(fname).stem if c not in '<>:"|?*\\/')
                safe = f"{kind}-{stem}{ext}"
                dst = up_dir / safe
                n = 1
                while dst.exists() and dst.read_bytes() != f["data"]:
                    dst = up_dir / f"{kind}-{stem}-{n}{ext}"
                    n += 1
                dst.write_bytes(f["data"])
                # ffprobe 校验真实音频流（MPT validate_audio 同思路）
                from video_director import prober
                probe = prober.probe_media(str(dst))
                if not probe.has_audio_stream:
                    dst.unlink(missing_ok=True)
                    self._send_json({"error": "文件不含音频流"}, 400)
                    return
                # 注册进 manifest（音频库扫描即出）
                manifest_path = lib_root / "audio_manifest.json"
                existing = []
                if manifest_path.is_file():
                    try:
                        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
                    except json.JSONDecodeError:
                        existing = []
                asset_id = f"{kind}-upload-{stem}"
                entry = {
                    "asset_id": asset_id,
                    "file": f"uploads/{dst.name}",
                    "type": kind,
                    "tags": [kind, "用户上传", stem],
                    "license": {"source": "user-uploaded", "commercial_use": True,
                                "attribution_required": False},
                }
                existing = [e for e in existing if e.get("asset_id") != asset_id]
                existing.append(entry)
                manifest_path.write_text(
                    json.dumps(existing, ensure_ascii=False, indent=2),
                    encoding="utf-8")
                self._send_json({
                    "ok": True, "asset_id": asset_id, "filename": dst.name,
                    "duration": probe.duration,
                })
            except Exception as exc:
                self._send_json({"ok": False, "error": f"上传失败: {exc}"}, 500)
            return

        # 多模型配置中心（四角色独立配置，整体保存）
        if parsed.path == "/api/model_config":
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON（需 UTF-8）"}, 400)
                return
            configs = payload.get("configs") or {}
            if not isinstance(configs, dict):
                self._send_json({"error": "configs 必须是对象"}, 400)
                return
            try:
                # __KEEP__ 占位 = 前端未改动该角色的 key，保留已存值
                existing = vd_model_config.load_all()
                for role, c in configs.items():
                    if isinstance(c, dict) and c.get("api_key") == "__KEEP__":
                        old = existing.get(role) or {}
                        c["api_key"] = old.get("api_key", "")
                vd_model_config.save_all(configs)
                vd_model_config.sync_llm_provider()  # llm 角色立即生效
                self._send_json({
                    "ok": True,
                    "roles": vd_model_config.status_for_frontend(),
                })
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        # LLM 文案润色（无 provider 时诚实降级原样返回）
        if parsed.path == "/api/enhance":
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON（需 UTF-8）"}, 400)
                return
            script = (payload.get("script") or "").strip()
            if not script:
                self._send_json({"error": "文案为空"}, 400)
                return
            try:
                enhanced = vd_llm.enhance_script(script)
                cfg = vd_llm.get_llm_config()
                self._send_json({
                    "ok": True,
                    "enhanced": enhanced,
                    "changed": enhanced != script,
                    "llm_enabled": cfg["enabled"],
                })
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        # 创作记忆反馈（👍👎）：record_feedback + 重聚合 Style DNA
        if parsed.path == "/api/feedback":
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON（需 UTF-8）"}, 400)
                return
            case_id = (payload.get("case_id") or "").strip()
            liked = payload.get("liked")
            note = (payload.get("note") or "").strip() or None
            if not case_id or liked is None:
                self._send_json({"error": "缺少 case_id 或 liked"}, 400)
                return
            try:
                from video_director import memory as vd_memory
                vd_memory.record_feedback(case_id, bool(liked), note)
                vd_memory.update_style_dna()  # 反馈后立即重聚合个人风格
                self._send_json({"ok": True, "case_id": case_id, "liked": bool(liked)})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        # 参考视频分析（风格重构非内容复制）：multipart 上传参考视频 → 镜头语法报告
        if parsed.path == "/api/reference":
            mp = _parse_multipart(self)
            files = mp["files"]
            refs = [f for f in files if f["name"] == "reference"]
            if not refs:
                self._send_json({"error": "请上传参考视频"}, 400)
                return
            project_id = f"web-{uuid.uuid4().hex[:8]}"
            project_dir = vd_config.project_dir(project_id)
            ref_dir = project_dir / "input" / "videos"
            ref_dir.mkdir(parents=True, exist_ok=True)
            try:
                from video_director import reference as vd_reference
                report = None
                for f in refs:
                    dst = ref_dir / Path(f["filename"]).name
                    dst.write_bytes(f["data"])
                    report = vd_reference.reference_analyzer(str(dst), str(project_dir))
                    break  # 单参考视频（多参考是后续扩展）
                if report is None:
                    raise RuntimeError("参考视频分析失败")
                self._send_json({
                    "ok": True,
                    "project_id": project_id,
                    "report": report,
                })
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        # Gate B 动态草稿（方案 §32/§83：确认渲染前先看低分辨率快速草稿）
        if parsed.path == "/api/draft":
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON（需 UTF-8）"}, 400)
                return
            pid = (payload.get("project_id") or "").strip()
            if not pid:
                self._send_json({"error": "缺少 project_id"}, 400)
                return
            try:
                from video_director import confirm as confirm_mod
                draft_path = confirm_mod.render_draft(pid)
                self._send_json({"ok": True, "draft_path": draft_path})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        # Director：一句话剪辑意图 → 读 Manifest+Context → 规划能力序列 → 执行 + verify
        if parsed.path == "/api/director/execute":
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON（需 UTF-8）"}, 400)
                return
            pid = (payload.get("project_id") or "").strip()
            instruction = (payload.get("instruction") or "").strip()
            if not pid or not instruction:
                self._send_json({"error": "缺少 project_id 或 instruction"}, 400)
                return
            try:
                from video_director import director as vd_director
                result = vd_director.direct(pid, instruction)
                self._send_json({"ok": True, "director": result})
            except vd_director.DirectorError as exc:
                self._send_json({"ok": False, "error": str(exc), "code": exc.code}, 400)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        # Stock 搜索（§24 统一入口 + §25 商业安全矩阵）
        if parsed.path == "/api/stock/search":
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON（需 UTF-8）"}, 400)
                return
            query = (payload.get("query") or "").strip()
            if not query:
                self._send_json({"error": "缺少 query"}, 400)
                return
            try:
                from video_director import stock as vd_stock
                hits = vd_stock.search_stock(
                    query, provider=payload.get("provider"),
                    orientation=payload.get("orientation"))
                cands = vd_stock.resolve_candidates(
                    hits, target_ratio=payload.get("ratio") or "9:16",
                    target_duration=payload.get("targetDuration"))
                self._send_json({"ok": True,
                                 "candidates": [c.to_dict() for c in cands]})
            except vd_stock.StockError as exc:
                self._send_json({"ok": False, "error": str(exc), "code": exc.code}, 503)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        # Revision Console：自然语言改稿 → 累积 edits → 应用到分镜书（预览，不落盘）
        if parsed.path == "/api/revision":
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON（需 UTF-8）"}, 400)
                return
            pid = (payload.get("project_id") or "").strip()
            instruction = (payload.get("instruction") or "").strip()
            direct_edits = payload.get("direct_edits")
            base_edits = payload.get("edits") or {}
            if not pid or (not instruction and not direct_edits):
                self._send_json({"error": "缺少 project_id 或 instruction/direct_edits"}, 400)
                return
            try:
                from video_director import confirm as confirm_mod
                sb_file = vd_config.project_dir(pid) / "plans" / "storyboard.json"
                sb = confirm_mod.load_storyboard(sb_file)
                if direct_edits:
                    delta = direct_edits
                else:
                    # LLM 优先（自然语言真实意义：随意说都理解），失败规则兜底
                    _llm = vd_llm.get_llm()
                    if _llm is not None:
                        delta = confirm_mod.parse_edit_instruction_llm(
                            instruction, sb, lambda p: _llm.generate(p)
                        )
                    else:
                        delta = {}
                    if not delta:
                        delta = confirm_mod.parse_edit_instruction(instruction, sb)
                if not delta:
                    self._send_json({
                        "ok": False,
                        "error": "未能识别的修改，请换一种说法（如：删掉第二段 / 第二段换成 3.mp4 / 第三段改成新文案）",
                    }, 400)
                    return
                full = confirm_mod.merge_edits(base_edits, delta)
                sb_for_resolve = dict(sb) if isinstance(sb, dict) else {}
                sb_for_resolve["project_id"] = pid
                full = confirm_mod.resolve_asset_references(full, sb_for_resolve)
                preview = confirm_mod.apply_storyboard_edits(sb, full)
                self._send_json({"ok": True, "storyboard": preview, "edits": full})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        # 确认渲染端点（Gate B 通过 → 正式渲染 final；edits 为空则渲染原分镜）
        if parsed.path == "/api/confirm":
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON（需 UTF-8）"}, 400)
                return
            pid = payload.get("project_id", "")
            edits = payload.get("edits") or None
            if not pid:
                self._send_json({"error": "缺少 project_id"}, 400)
                return
            from video_director import confirm as confirm_mod
            try:
                final = confirm_mod.finalize_after_review(pid, edits=edits)
                # 创作记忆接线：确认渲染的 final 记录/补全 CreationCase。
                # preview 模式 compose 已存 draft（分镜书字段），这里只补 final，
                # 保证 👍👎 时 extract_preference_delta 有 diff 源。
                case_id = ""
                try:
                    from video_director import memory as vd_memory
                    from video_director.memory import CreationCase
                    existing = vd_memory.get_case(pid)
                    plans_dir = vd_config.project_dir(pid) / "plans"
                    sb: dict = {}
                    for name in ("storyboard.json", "scenes.json"):
                        f = plans_dir / name
                        if f.is_file():
                            try:
                                sb = json.loads(f.read_text(encoding="utf-8"))
                                break
                            except json.JSONDecodeError:
                                continue
                    if isinstance(sb, list):  # scenes.json 是 list
                        sb = {"scenes": sb, "scene_count": len(sb)}
                    draft = (existing or {}).get("draft_artifact") or {
                        "style": sb.get("style_id", "") if isinstance(sb, dict) else "",
                        "scene_count": (sb.get("scene_count", 0)
                                        if isinstance(sb, dict) else 0),
                        "total_duration": 0.0,
                    }
                    final_artifact = {
                        "style": draft.get("style", ""),
                        "scene_count": draft.get("scene_count", 0),
                        "total_duration": round(
                            float(final.narration_duration or 0), 2),
                        "final_path": str(final.final_path),
                    }
                    case = CreationCase(
                        case_id=pid,
                        input_type="script",
                        script=(existing or {}).get("script", ""),
                        user_brief=(existing or {}).get("user_brief"),
                        assets=(existing or {}).get("assets", []),
                        initial_storyboard=(existing or {}).get(
                            "initial_storyboard", sb if isinstance(sb, dict) else {}),
                        draft_artifact=draft,
                        final_artifact=final_artifact,
                    )
                    vd_memory.record_case(case)
                    case_id = pid
                except Exception as exc:
                    print(f"[web] record_case(confirm) 失败（不阻塞）: {exc}",
                          file=sys.stderr)
                self._send_json({
                    "ok": True,
                    "case_id": case_id,
                    "final_path": final.final_path,
                    "narration_duration": final.narration_duration,
                    "qc": final.qc_report,
                    "warnings": final.warnings,
                })
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        # ===================== Graphics Runtime 端点（HyperFrames 集成）=====================

        if parsed.path == "/api/graphics/plan":
            # GFX-019 Text → Graphic Plan（纯文案 → 可编辑创作方案，方案 §34 先看方案再渲染）
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON"}, 400)
                return
            script = (payload.get("script") or "").strip()
            if not script:
                self._send_json({"error": "请先输入文案"}, 400)
                return
            try:
                from video_director.graphics import director as vd_gfx_director
                td = payload.get("target_duration")
                plan = vd_gfx_director.build_graphic_plan(
                    script,
                    content_type=payload.get("content_type") or "knowledge",
                    style_id=payload.get("style_id") or "editorial",
                    target_duration=float(td) if td else None,
                    width=int(payload.get("width") or 1080),
                    height=int(payload.get("height") or 1920),
                )
                self._send_json({"ok": True, "plan": plan})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        if parsed.path == "/api/graphics/preview":
            # GFX-013 Graphic Preview：plan → 确定性 HTML（iframe srcdoc 即时预览）
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON"}, 400)
                return
            plan = payload.get("plan")
            if not plan or not plan.get("scenes"):
                self._send_json({"error": "缺少 plan（先 /api/graphics/plan 生成）"}, 400)
                return
            try:
                from video_director.graphics import director as vd_gfx_director
                from video_director.graphics.preflight import deterministic_preflight
                html = vd_gfx_director.plan_to_html(plan)
                rep = deterministic_preflight(html)
                if rep.status != "PASS":
                    self._send_json({"ok": False, "error": "确定性预检失败: " + "; ".join(rep.issues)}, 400)
                    return
                self._send_json({"ok": True, "html": html, "sandbox": {
                    "isolated_origin": True, "block_network": True}})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        if parsed.path == "/api/catalog/blocks/create":
            # §42 Block 开发体验：New Block → BlockDefinition → 进 Creative Catalog
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON"}, 400)
                return
            try:
                from video_director.graphics import blocks as vd_gfx_blocks
                block_id = (payload.get("id") or "").strip()
                name = (payload.get("name") or "").strip()
                base_block = payload.get("base_block") or "kb.title"
                intents = payload.get("semantic_intents") or ["custom"]
                if not block_id.startswith("kb.") and not block_id.startswith("pd."):
                    self._send_json({"ok": False, "error": "block id 须以 kb./pd. 开头"}, 400)
                    return
                if not name:
                    self._send_json({"ok": False, "error": "块名不能为空"}, 400)
                    return
                # 以既有块为渲染基件（§42：定义语义/槽位/意图，视觉结构复用基件）
                try:
                    base = vd_gfx_blocks.get_block(base_block)
                except KeyError:
                    self._send_json({"ok": False, "error": f"base_block {base_block} 不存在"}, 400)
                    return
                # 持久化用户块定义（storage/composer/custom_blocks.json；加载时并入 catalog）
                from dataclasses import replace
                custom_dir = STORAGE / "composer"
                custom_dir.mkdir(parents=True, exist_ok=True)
                cfile = custom_dir / "custom_blocks.json"
                defs = []
                if cfile.is_file():
                    try:
                        defs = json.loads(cfile.read_text(encoding="utf-8"))
                    except json.JSONDecodeError:
                        defs = []
                defs = [d for d in defs if d.get("id") != block_id]
                defs.append({
                    "id": block_id, "name": name, "category": base.category,
                    "semantic_intents": list(intents), "slots": list(base.slots),
                    "duration_min": base.duration_min, "duration_max": base.duration_max,
                    "motion": payload.get("motion") or base.motion,
                    "base_block": base_block,
                })
                cfile.write_text(json.dumps(defs, ensure_ascii=False, indent=2), encoding="utf-8")
                # 注册进运行时 catalog（本进程立即可用）
                vd_gfx_blocks.register_custom_block(block_id, name, base.category,
                                                    tuple(intents), base.slots,
                                                    base.duration_min, base.duration_max,
                                                    payload.get("motion") or base.motion,
                                                    base_block)
                self._send_json({"ok": True, "id": block_id, "name": name})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        if parsed.path == "/api/gfx/gen_asset":
            # §34 生成素材：为场景生成配图（image 角色）。未配置 key → 诚实失败。
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON"}, 400)
                return
            plan = payload.get("plan") or {}
            i = payload.get("scene_index")
            scenes = plan.get("scenes") or []
            if i is None or not (0 <= int(i) < len(scenes)):
                self._send_json({"ok": False, "error": "scene_index 越界"}, 400)
                return
            try:
                from video_director import model_config as vd_mc
                from video_director import generation as vd_generation
                img_cfg = vd_mc.get_role_config("image")
                if not (img_cfg or {}).get("api_key"):
                    self._send_json({"ok": False,
                                     "error": "image 角色未配置 API key（生成素材需生图能力；当前可先用程序化视觉块）"}, 503)
                    return
                # §34 生成素材：为场景生成配图（roles["image"] AI 生图，已接线）
                scene_text = scenes[int(i)].get("text") or scenes[int(i)].get("role") or ""
                prompt = scene_text or payload.get("prompt") or "视觉配图"
                try:
                    imgs = vd_generation.generate_image_variants(
                        prompt, width=int(payload.get("width") or 1080),
                        height=int(payload.get("height") or 1920), count=1)
                except vd_generation.GenerationError as gexc:
                    self._send_json({"ok": False, "error": str(gexc), "code": gexc.code}, 503)
                    return
                item = imgs[0]
                self._send_json({
                    "ok": True,
                    "image_b64": __import__("base64").b64encode(item["data"]).decode("ascii"),
                    "variant": item.get("variant"),
                    "model": item.get("model"),
                })
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        if parsed.path == "/api/graphics/render":
            # GFX-006/007：plan → 编译 → Playwright 逐帧 → ffmpeg → MP4（项目落盘）
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON"}, 400)
                return
            plan = payload.get("plan")
            quality = payload.get("quality") or "draft"
            if not plan or not plan.get("scenes"):
                self._send_json({"error": "缺少 plan"}, 400)
                return
            pid = "gfx-" + uuid.uuid4().hex[:8]
            try:
                from video_director.graphics import director as vd_gfx_director
                from video_director.graphics import render as vd_gfx_render
                program = vd_gfx_director.instantiate_graphic_program(plan, program_id=pid)
                base = vd_config.project_dir(pid)
                vd_config.init_project_dirs(base)
                out = str(base / "output" / "final.mp4")
                vd_gfx_render.render_graphic_program(program, out, quality=quality)
                # 项目登记（项目页/首页可见）
                (base / "plans" / "graphic_plan.json").write_text(
                    json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
                (base / "input" / "script.txt").write_text(
                    (payload.get("script") or ""), encoding="utf-8")
                try:
                    vd_store.create_project(pid, "[图形] " + (plan.get("content_type") or "knowledge"), {
                        "mode": "graphic", "quality": quality})
                    vd_store.finish_project(pid, out, program.duration, {"verdict": "SKIP"})
                except Exception as exc:
                    print(f"[web] create_project(gfx) 失败（不阻塞）: {exc}", file=sys.stderr)
                self._send_json({"ok": True, "project_id": pid, "final_path": out,
                                 "duration": program.duration,
                                 "frames": program.total_frames})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        # ===================== Shotcraft 集成端点（Registry/Editor/Proposal）=====================

        if parsed.path in ("/api/registry/shots/resolve", "/api/registry/motions/resolve",
                           "/api/registry/templates/resolve"):
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON"}, 400)
                return
            try:
                from video_director.registries import resolver as vd_resolver
                if parsed.path.endswith("shots/resolve"):
                    cands = vd_resolver.resolve_shots(vd_resolver.ShotResolveRequest(
                        semantic_intents=tuple(payload.get("semantic_intents") or ()),
                        content_type=payload.get("content_type") or "",
                        style_id=payload.get("style_id"),
                        duration=float(payload.get("duration") or 4.0),
                        available_asset_types=tuple(payload.get("available_asset_types") or ()),
                        scene_position=payload.get("scene_position"),
                    ))
                elif parsed.path.endswith("motions/resolve"):
                    cands = vd_resolver.resolve_motions(vd_resolver.MotionResolveRequest(
                        layer_type=payload.get("layer_type") or "image",
                        semantic_intent=payload.get("semantic_intent") or "enter",
                        style_id=payload.get("style_id"),
                        duration=float(payload.get("duration") or 0.5),
                    ))
                else:
                    cands = vd_resolver.resolve_templates(vd_resolver.TemplateResolveRequest(
                        content_type=payload.get("content_type") or "",
                        ratio=payload.get("ratio") or "9:16",
                        duration=float(payload.get("duration") or 20),
                        style_id=payload.get("style_id"),
                    ))
                self._send_json({"ok": True, "candidates": [
                    {"recipe_id": c.recipe_id, "version": c.version, "name": c.name,
                     "compatibility": c.compatibility, "reasons": list(c.reasons),
                     "missing_requirements": list(c.missing_requirements)}
                    for c in cands
                ]})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        if parsed.path == "/api/templates/instantiate":
            # 模板试用：Template → Composition → 建项目（可编辑实例，非 MP4）
            # multipart：template_id/ratio/slot_values_json 文本域 + <slot>_file 文件域
            mp = _parse_multipart(self)
            fields, files = mp["fields"], mp["files"]
            template_id = fields.get("template_id", "")
            ratio = fields.get("ratio", "9:16")
            try:
                slot_values = json.loads(fields.get("slot_values_json", "{}"))
            except json.JSONDecodeError:
                slot_values = {}
            if not template_id:
                self._send_json({"error": "缺少 template_id"}, 400)
                return
            try:
                import uuid as _uuid
                from video_director.registries import compiler as vd_compiler
                pid = "tpl-" + _uuid.uuid4().hex[:8]
                base = vd_config.project_dir(pid)
                dirs = vd_config.init_project_dirs(base)
                assets = {}
                # 文件槽：落盘到项目 input/videos 并探测（asset_id 按槽位语义回填）
                for f in files:
                    if not f["name"].endswith("_file"):
                        continue
                    slot_key = f["name"][:-5]
                    raw = f["filename"] or "slot.mp4"
                    safe = re.sub(r"[^\w\-.]+", "_", raw) or "slot.mp4"
                    dest = dirs["input"] / "videos" / safe
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(f["data"])
                    probe = vd_prober.probe_media(str(dest))
                    is_image = (probe.has_video_stream is False and probe.has_audio_stream is False) \
                               or safe.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
                    aid = f"asset-{len(assets) + 1:02d}"
                    from video_director.models import AssetRef
                    aref = AssetRef(id=aid, type="image" if is_image else "video",
                                    file=str(dest), duration=probe.duration,
                                    width=probe.width, height=probe.height,
                                    fps=probe.fps, source="user")
                    assets[aid] = aref
                    slot_values[slot_key] = aid  # 槽位 → 素材绑定
                if not assets:
                    self._send_json({"error": "模板试用请至少上传一张素材（素材槽位旁选择文件）"}, 400)
                    return
                ir = vd_compiler.instantiate_template(
                    template_id, fields.get("template_version") or None, ratio,
                    slot_values, assets, pid,
                )
                # 落盘 composition.json + manifest（Editor 打开的事实源）
                comp_path = dirs["plans"] / "composition.json"
                comp_path.write_text(json.dumps(json.loads(ir.model_dump_json()),
                                                 ensure_ascii=False, indent=2), encoding="utf-8")
                (dirs["assets"] / "manifest.json").write_text(
                    json.dumps([a.model_dump() for a in assets.values()],
                               ensure_ascii=False, indent=2), encoding="utf-8")
                (dirs["input"] / "script.txt").write_text(
                    "模板实例：" + template_id, encoding="utf-8")
                vd_store.create_project(pid, f"[模板] {template_id}",
                                        {"mode": "template", "ratio": ratio,
                                         "template_id": template_id})
                vd_store.set_stage(pid, vd_store.Stage.AWAITING_CONFIRM)
                self._send_json({"ok": True, "project_id": pid,
                                 "origin_template_id": ir.origin_template_id,
                                 "origin_template_version": ir.origin_template_version,
                                 "scene_count": len(ir.scenes),
                                 "duration": ir.project.duration})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        if parsed.path == "/api/editor/command":
            # Editor Command（UI → 命令 → 校验 → Revision；前端不得直改 IR）
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON"}, 400)
                return
            pid = payload.get("project_id", "")
            if not pid:
                self._send_json({"error": "缺少 project_id"}, 400)
                return
            try:
                from video_director import editor as vd_editor
                result = vd_editor.execute_command(pid, {
                    "command": payload.get("command"),
                    "targetId": payload.get("targetId"),
                    "payload": payload.get("payload") or {},
                    "expectedVersion": payload.get("expectedVersion"),
                    "author": payload.get("author", "user"),
                })
                self._send_json({
                    "ok": result.status == "APPLIED",
                    "status": result.status,
                    "revision_id": result.revision_id,
                    "composition_version": result.composition_version,
                    "changed_artifacts": result.changed_artifacts,
                    "reused_artifacts": result.reused_artifacts,
                    "stale_artifacts": result.stale_artifacts,
                    "errors": result.errors,
                    "composition": result.composition,
                })
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        if parsed.path in ("/api/proposal/propose", "/api/proposal/preview",
                           "/api/proposal/apply", "/api/proposal/reject"):
            # AI 提案式修订四态流（Proposal ≠ Apply）
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON"}, 400)
                return
            pid = payload.get("project_id", "")
            if not pid:
                self._send_json({"error": "缺少 project_id"}, 400)
                return
            try:
                from video_director import proposal as vd_proposal
                if parsed.path.endswith("propose"):
                    out = vd_proposal.propose(pid, payload.get("instruction", ""))
                elif parsed.path.endswith("preview"):
                    out = vd_proposal.preview(pid, payload.get("proposal_id", ""))
                elif parsed.path.endswith("apply"):
                    out = vd_proposal.apply(pid, payload.get("proposal_id", ""))
                else:
                    out = vd_proposal.reject(pid, payload.get("proposal_id", ""))
                self._send_json({"ok": True, **out})
            except vd_proposal.ProposalError as exc:
                self._send_json({"ok": False, "error": str(exc), "code": exc.code}, 400)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        if parsed.path == "/api/style_dna/record":
            # §三十二 Style DNA：记录明确行为（used/pinned/liked/rejected/avoided）
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON"}, 400)
                return
            try:
                from video_director import memory as vd_memory
                ok = vd_memory.record_motion_behavior(
                    payload.get("item_id", ""), payload.get("behavior", ""),
                    item_kind=payload.get("item_kind", "motion"),
                    project_id=payload.get("project_id"),
                )
                if not ok:
                    self._send_json({"ok": False, "error": "行为不在白名单或 item_id 为空"}, 400)
                    return
                self._send_json({"ok": True})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        if parsed.path == "/api/editor/generate":
            # EDT-008 Timeline-native Generation：生成素材（image/graphic/video 程序化）
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON"}, 400)
                return
            pid = payload.get("project_id", "")
            if not pid:
                self._send_json({"error": "缺少 project_id"}, 400)
                return
            try:
                from video_director import generation as vd_generation
                results = vd_generation.generate(
                    project_id=pid, prompt=payload.get("prompt", ""),
                    gen_type=payload.get("type", "image"),
                    width=int(payload.get("width") or 1080),
                    height=int(payload.get("height") or 1920),
                    model=payload.get("model") or None,
                    reference=payload.get("reference") or None,
                )
                self._send_json({"ok": True, "results": [
                    {"asset_id": r.asset_id, "variant": r.variant, "type": r.type,
                     "file": r.file, "input_fingerprint": r.input_fingerprint,
                     "output_fingerprint": r.output_fingerprint, "provider_id": r.provider_id}
                    for r in results
                ]})
            except vd_generation.GenerationError as exc:
                self._send_json({"ok": False, "error": str(exc), "code": exc.code},
                                503 if exc.code == "GENERATION_UNAVAILABLE" else 400)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        if parsed.path == "/api/editor/generate/add":
            # EDT-008 Add to Timeline：生成结果接入时间线（走 editor generate_media 命令）
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON"}, 400)
                return
            pid = payload.get("project_id", "")
            scene_id = payload.get("scene_id", "")
            asset_id = payload.get("asset_id", "")
            if not (pid and scene_id and asset_id):
                self._send_json({"error": "缺少 project_id/scene_id/asset_id"}, 400)
                return
            try:
                from video_director import generation as vd_generation
                out = vd_generation.add_to_timeline(pid, scene_id, asset_id)
                self._send_json({"ok": out["status"] == "APPLIED", **out})
            except vd_generation.GenerationError as exc:
                self._send_json({"ok": False, "error": str(exc), "code": exc.code}, 400)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        if parsed.path != "/api/compose":
            self._send_json({"error": "not found"}, 404)
            return

        mp = _parse_multipart(self)
        fields, files = mp["fields"], mp["files"]

        script = fields.get("script", "").strip()
        if not script:
            self._send_json({"error": "请先粘贴文案"}, 400)
            return
        videos = [f for f in files if f["name"] == "videos"]
        images = [f for f in files if f["name"] == "images"]
        mode = fields.get("mode", "auto")  # auto | product | script_only | image_shots
        if mode not in ("auto", "product", "script_only", "image_shots"):
            self._send_json({"error": f"未知模式: {mode}"}, 400)
            return
        if mode in ("auto", "product", "image_shots") and not videos and not images:
            if mode == "product":
                self._send_json({"error": "产品广告片模式请上传产品图片"}, 400)
            elif mode == "image_shots":
                self._send_json({"error": "一图多镜头模式请上传一张图片"}, 400)
            else:
                self._send_json({"error": "请至少上传视频或图片素材（纯文案请切换「纯文案成片」模式）"}, 400)
            return
        if mode == "product" and not images:
            self._send_json({"error": "产品广告片模式请上传产品图片"}, 400)
            return
        if mode == "image_shots" and not images:
            self._send_json({"error": "一图多镜头模式请上传一张图片"}, 400)
            return

        w_str, h_str = fields.get("aspect", "1080x1920").split("x")
        width, height = int(w_str), int(h_str)
        voice = fields.get("voice", "zh-CN-XiaoxiaoNeural")
        # 语速：前端倍速（0.9/1.0/1.3…）→ edge-tts rate 相对百分比
        _vr = fields.get("voice_rate", "1.0")
        try:
            _spd = float(_vr)
            voice_rate = f"{int(round((_spd - 1.0) * 100)):+d}%"
        except ValueError:
            voice_rate = "+0%"
        style_intent = fields.get("style_intent", "") or None
        bgm = fields.get("bgm", "") or None
        sfx_choice = fields.get("sfx", "auto")  # auto | "" | 具体 asset_id
        confirm = fields.get("confirm_mode", "preview")  # preview | direct

        project_id = f"web-{uuid.uuid4().hex[:8]}"
        # 上传落点与 pipeline 产物统一（config.project_dir → storage/composer/<pid>）
        project_dir = vd_config.project_dir(project_id)
        videos_dir = project_dir / "input" / "videos"
        images_dir = project_dir / "input" / "images"
        videos_dir.mkdir(parents=True, exist_ok=True)
        images_dir.mkdir(parents=True, exist_ok=True)

        video_paths = []
        for f in videos:
            dst = videos_dir / Path(f["filename"]).name
            dst.write_bytes(f["data"])
            video_paths.append(str(dst))
        image_paths = []
        for f in images:
            dst = images_dir / Path(f["filename"]).name
            dst.write_bytes(f["data"])
            image_paths.append(str(dst))

        t0 = time.time()
        try:
            if mode == "script_only":
                # 纯文案模式：无需素材
                from video_director import script_only as vd_script_only
                result = vd_script_only.compose_script_only(
                    script=script,
                    project_id=project_id,
                    voice=voice,
                    style_intent=style_intent,
                    width=width,
                    height=height,
                )
            elif mode == "product":
                # 产品广告片模式：图片=产品图，无需视频
                from video_director import product_promo as vd_promo
                if not image_paths:
                    self._send_json({"error": "产品广告片模式请上传产品图片"}, 400)
                    return
                result = vd_promo.compose_product_promo(
                    script=script,
                    product_image_paths=image_paths,
                    project_id=project_id,
                    voice=voice,
                    style_intent=style_intent,
                    width=width,
                    height=height,
                )
            elif mode == "image_shots":
                # 一图多镜头（§29 hero/detail/macro）：单图 → 三镜头递进动效片
                # 诚实边界：纯视觉镜头语言产物，不带旁白配音
                from video_director import image_editor as vd_image_editor
                ir = vd_image_editor.compose_image_shots(
                    image_path=image_paths[0],
                    project_id=project_id,
                    texts=None,
                    width=width,
                    height=height,
                )
                # 独立返回结构（前端 renderResult 消费相同字段）
                self._send_json({
                    "ok": True,
                    "elapsed_s": round(time.time() - t0, 1),
                    "project_id": project_id,
                    "case_id": "",
                    "mode": mode,
                    "pending_confirm": False,
                    "storyboard": None,
                    "final_path": ir.final_path,
                    "narration_duration": ir.total_duration,
                    "qc": {"verdict": "SKIP", "checks": []},
                    "warnings": list(ir.warnings) + ["一图多镜头模式为纯视觉产物（无配音）"],
                })
                return
            elif video_paths or image_paths:
                # SFX 选择 → sfx_plan：
                #   auto → None（走 planner/风格的默认 action 绑定）
                #   ""   → 显式空列表（关闭 SFX）
                #   其他 → 单音效循环铺底（每场景边界放一次）
                sfx_plan = None
                if sfx_choice == "":
                    sfx_plan = []
                elif sfx_choice not in ("auto",):
                    sfx_plan = [{"at": i * 4.0, "asset_id": sfx_choice,
                                 "role": "ambience"} for i in range(200)]
                result = pipeline.compose_auto(
                    script=script,
                    video_files=video_paths,
                    image_files=image_paths,
                    project_id=project_id,
                    voice=voice,
                    voice_rate=voice_rate,
                    style_intent=style_intent,
                    bgm_asset_id=bgm,
                    sfx_plan=sfx_plan,
                    width=width,
                    height=height,
                    auto_confirm=(confirm == "direct"),
                    subtitle_mode=fields.get("subtitle_mode", "plain"),
                    title=fields.get("title", "") or None,
                    lower_third_name=fields.get("lower_third_name", "") or None,
                    lower_third_role=fields.get("lower_third_role", "") or None,
                    creative_brief=fields.get("creative_brief", "") or None,
                    watermark=fields.get("watermark", "AI Video Director") or None,
                )
            else:
                self._send_json({"error": "无素材"}, 400)
                return

            # 创作记忆接线：成片成功 → 自动 record_case（消灭孤儿模块）
            # draft_artifact = 初始方案（分镜书；direct 模式无分镜书用未知占位），
            # final_artifact = 实际成片字段。👍👎 时 extract_preference_delta
            # 对比两者产出 prefer/avoid（如 style=xx / scene_count=N / 时长）。
            case_id = ""
            try:
                from video_director import memory as vd_memory
                from video_director.memory import CreationCase
                final_ok = bool(getattr(result, "final_path", ""))
                pending = bool(getattr(result, "pending_confirm", False))
                sb = getattr(result, "storyboard", None) or {}
                if isinstance(sb, dict) and sb:
                    draft_artifact = {
                        "style": sb.get("style_id") or "",
                        "scene_count": sb.get("scene_count",
                                               len(sb.get("scenes", []) or [])),
                        "total_duration": round(
                            float(sb.get("total_duration", 0) or 0), 2),
                    }
                else:
                    # direct 模式无返回分镜书，从落盘 plans/ 读真实初始方案
                    draft_artifact = _read_project_draft(project_id)
                final_artifact: dict = {}
                if final_ok:
                    final_artifact = {
                        "style": draft_artifact.get("style", ""),
                        "scene_count": draft_artifact["scene_count"],
                        "total_duration": round(
                            float(getattr(result, "narration_duration", 0) or 0), 2),
                    }
                if final_ok or pending:
                    case = CreationCase(
                        case_id=project_id,
                        input_type="script",
                        script=script,
                        user_brief=style_intent,
                        assets=[Path(v).name for v in video_paths]
                               + [Path(i).name for i in image_paths],
                        initial_storyboard=sb if isinstance(sb, dict) else {},
                        draft_artifact=draft_artifact,
                        final_artifact=final_artifact,
                    )
                    vd_memory.record_case(case)
                    case_id = case.case_id
            except Exception as exc:
                print(f"[web] record_case 失败（不阻塞成片）: {exc}", file=sys.stderr)

            # 返回 JSON（video 走 /file?path= 播放）
            self._send_json({
                "ok": True,
                "elapsed_s": round(time.time() - t0, 1),
                "project_id": project_id,
                "case_id": case_id,
                "mode": mode,
                "pending_confirm": bool(getattr(result, "pending_confirm", False)),
                "storyboard": getattr(result, "storyboard", None),
                "final_path": result.final_path,
                "narration_duration": result.narration_duration,
                "qc": result.qc_report,
                "warnings": result.warnings,
            })
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, 500)


def main(port: int = 8501):
    _load_llm_config()
    try:
        vd_audio.ensure_audio_library()
        # 扩库：风格化 BGM（ffmpeg 合成免费，中文标签），幂等
        vd_audio.generate_style_bgm_library()
    except Exception as exc:
        print(f"[web] 音频库自检失败（不阻塞）: {exc}", file=sys.stderr)
    # §42 Block 开发体验：启动时加载用户自定义块进 Creative Catalog
    try:
        from video_director.graphics import blocks as _vd_gfx_blocks
        n = _vd_gfx_blocks.load_custom_blocks(STORAGE)
        if n:
            print(f"[web] 自定义块加载 {n} 个进 Creative Catalog", file=sys.stderr)
    except Exception as exc:
        print(f"[web] 自定义块加载失败（不阻塞）: {exc}", file=sys.stderr)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"AI Video Director → http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8501)