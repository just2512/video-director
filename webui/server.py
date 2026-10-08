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
from urllib.parse import parse_qs, quote as urllib_quote, urlparse

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
# §五 测试/生产 storage 隔离：项目数据根统一走 config.composer_storage_root()
# （VIDEO_DIRECTOR_STORAGE_ROOT）。STORAGE = 该根父目录（staging 等非项目杂项随其隔离）。
COMPOSER_ROOT = vd_config.composer_storage_root()
STORAGE = COMPOSER_ROOT.parent
LLM_CONFIG_FILE = COMPOSER_ROOT / "llm_config.json"

# ---------------------------------------------------------------------------
# §48 SSE 进度推送：事件总线（Director/生成/渲染过程事件 → 前端 EventSource）
# ---------------------------------------------------------------------------
import queue as _queue_mod

#: 全局 SSE 事件队列（多订阅者各持一个 Queue；无订阅者时事件即丢，不积压）
_SSE_SUBSCRIBERS: list = []
_SSE_LOCK = threading.Lock()
# §23/§46 前端 Selection 上报（Command Bar 上下文；单机单用户内存态）
_DIRECTOR_SELECTION: dict = {}


def sse_publish(event: str, data: dict) -> None:
    """广播一条 SSE 事件（§48：进度推送；无订阅者时静默丢弃）。"""
    msg = json.dumps({"event": event, "data": data}, ensure_ascii=False)
    with _SSE_LOCK:
        dead = []
        for q in _SSE_SUBSCRIBERS:
            try:
                q.put_nowait(msg)
            except _queue_mod.Full:
                dead.append(q)  # 慢消费者丢事件不阻塞广播
        for q in dead:
            _SSE_SUBSCRIBERS.remove(q)


def sse_subscribe():
    """新订阅者注册（返回其专属 Queue）。"""
    q = _queue_mod.Queue(maxsize=200)
    with _SSE_LOCK:
        _SSE_SUBSCRIBERS.append(q)
    return q


def sse_unsubscribe(q) -> None:
    with _SSE_LOCK:
        if q in _SSE_SUBSCRIBERS:
            _SSE_SUBSCRIBERS.remove(q)


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


def _motion_grammars() -> list:
    """§十八 艺术动画：Huashu grammar 清单（generation.availability.motion）。"""
    try:
        from video_director import procedural_motion as _pm
        return _pm.available_grammars()
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
            root = COMPOSER_ROOT
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
        elif route.startswith("/static/"):
            # §8 Frontend module split：ES Module / CSS 静态资源
            # 路径穿越防护：resolve 后必须仍在 STATIC_DIR 内
            rel = route[len("/static/"):]
            target = (STATIC_DIR / rel).resolve()
            if (not str(target).startswith(str(STATIC_DIR.resolve()))
                    or not target.is_file()):
                self._send_json({"error": "not found"}, 404)
                return
            self._send_html(target)
        elif route == "/api/health":
            self._send_json({"ok": True, "app": "AI Video Director"})
        elif route == "/api/events":
            # §48 SSE 进度推送：text/event-stream 长连接（Director/生成/渲染事件）
            q = sse_subscribe()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                # 初始 hello 事件（前端确认连接建立）
                self.wfile.write(b"event: hello\ndata: {\"connected\": true}\n\n")
                self.wfile.flush()
                import queue as _q
                while True:
                    try:
                        msg = q.get(timeout=15)
                        self.wfile.write(f"data: {msg}\n\n".encode("utf-8"))
                    except _q.Empty:
                        self.wfile.write(b": keepalive\n\n")  # 心跳防代理断连
                    self.wfile.flush()
            except (ConnectionAbortedError, ConnectionResetError, OSError):
                pass
            finally:
                sse_unsubscribe(q)
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
        elif route == "/api/settings/export":
            # §52 配置导出：角色/模型/地址结构——api_key 剥离（密钥不落导出文件）
            try:
                roles = vd_model_config.status_for_frontend()
                safe_roles = [{k: v for k, v in r.items() if k != "api_key"}
                              for r in roles]
                roles = vd_model_config.status_for_frontend()
                stock_state = {
                    name: any(r.get("enabled") for r in roles
                               if r.get("role") == f"stock_{name}")
                    for name in ("pexels", "pixabay", "coverr")
                }
                self._send_json({
                    "ok": True, "version": 1, "exported_at": time.time(),
                    "models": safe_roles, "stock_configured": stock_state,
                    "note": "密钥不包含在导出文件中；导入后需在本机重新填写。",
                })
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
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
            cache_dir = COMPOSER_ROOT / "voice_previews"
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
                # §12/§31.1 Generation Availability：AI 生图/生视频是否可用
                # 前端据此 disable 按钮 + 显示原因（不先允许点击再 503）
                try:
                    from video_director import model_config as _mc
                    data.append({
                        "name": "generation.availability",
                        "status": "AVAILABLE",
                        "generation": {
                            "image": {
                                "available": _mc.is_role_enabled("image"),
                                "reason": "" if _mc.is_role_enabled("image")
                                else "未配置图片生成服务（设置 → 模型里填写生图服务）",
                            },
                            "video": {
                                "available": _mc.is_role_enabled("video"),
                                "reason": "" if _mc.is_role_enabled("video")
                                else "未配置视频生成服务（设置 → 模型里填写生视频服务）",
                            },
                            # §十八 艺术动画：Huashu Procedural Motion 本地确定性
                            # 渲染，无外部 provider；grammar 清单随可用性下发，
                            # 前端不用再发第二个请求（零新端点）。
                            "motion": {
                                "available": True,
                                "reason": "",
                                "grammars": _motion_grammars(),
                            },
                        },
                        "providers": [],
                        "supports": [],
                        "fallback_tools": [],
                    })
                except Exception:
                    logger.debug("generation availability 读取失败（忽略）")
                self._send_json(data)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
        elif route == "/api/continuity":
            # §13 ContinuityVerifier（PPT Detector）：审阅页连续性检查
            try:
                from video_director import continuity as vd_continuity
                from video_director import editor as vd_editor_mod
                qs = parse_qs(parsed.query)
                pid = (qs.get("project_id") or [""])[0]
                if not pid:
                    self._send_json({"error": "缺少 project_id"}, 400)
                    return
                ir = vd_editor_mod.load_composition(pid)
                report = vd_continuity.verify_composition_continuity(ir)
                self._send_json({
                    "status": report.status,
                    "issues": [i.model_dump() for i in report.issues],
                })
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
            # §52 Project Index：服务端解释状态（status/editable/legacy/actions），
            # 前端不猜。支持 q（搜索）/ filter（状态筛选）/ limit。
            try:
                qs_ = parse_qs(parsed.query)
                from video_director import project_manager as vd_pm
                q_ = qs_.get("q", [""])[0]
                filter_ = qs_.get("filter", ["all"])[0]
                limit_ = int(qs_.get("limit", ["200"])[0])
                projects = vd_pm.list_index(q=q_, name_filter=filter_, limit=limit_)
                self._send_json({"projects": projects, "total": len(projects)})
            except Exception as exc:
                import traceback
                print(f"[web] /api/projects 500: {exc}\n{traceback.format_exc()}",
                      file=sys.stderr)
                self._send_json({"error": str(exc)}, 500)
        elif route.startswith("/api/projects/") and route.endswith("/draft_info"):
            # §46 Preview：项目草稿存在性 + 路径（顶栏 Preview 按钮消费）
            pid = route[len("/api/projects/"):-len("/draft_info")]
            try:
                pdir = COMPOSER_ROOT / pid
                draft = pdir / "output" / "draft.mp4"
                final = pdir / "output" / "final.mp4"
                self._send_json({
                    "project_id": pid,
                    "draft_path": str(draft) if draft.is_file() else "",
                    "draft_exists": draft.is_file(),
                    "final_path": str(final) if final.is_file() else "",
                })
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
        elif route == "/api/project_detail":
            # 项目详情（重开续用：产物清单 + 分镜 + IR 概览 + §52 状态字段）
            qs = parse_qs(parsed.query)
            pid = qs.get("project_id", [""])[0]
            if not pid:
                self._send_json({"error": "缺少 project_id"}, 400)
                return
            try:
                pdir = COMPOSER_ROOT / pid
                if not pdir.is_dir():
                    self._send_json({"error": "项目不存在"}, 404)
                    return
                detail = {"project_id": pid}
                # §52 增强：Project Manager 统一解释状态（name/status/editable/
                # legacy/actions——前端详情页与列表同源，不两套推导）
                try:
                    from video_director import project_manager as vd_pm
                    detail.update(vd_pm.inspect(pid))
                except Exception as exc:  # noqa: BLE001 inspect 失败不阻断旧字段
                    print(f"[web] project inspect({pid}) 失败: {exc}", file=sys.stderr)
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
            # §26 Asset Library：storage/assets 全局台账 + 各项目 manifest 合并视图
            try:
                from video_director import asset_catalog as vd_acat
                qs = parse_qs(parsed.query)
                lib_entries = vd_acat.list_assets(
                    kind=qs.get("type", [""])[0] or None,
                    source=qs.get("source", [""])[0] or None,
                    q=qs.get("q", [""])[0],
                    only_clear=qs.get("only_clear", [""])[0] == "1")
                lib = {e["asset_id"]: dict(e, used_in=[]) for e in lib_entries}
                # 项目 manifest 合并（老项目素材照常出现，含 used_in）
                root = COMPOSER_ROOT
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
                            if a.get("source"):
                                ent["source"] = a["source"]
                            if d.name not in ent["used_in"]:
                                ent["used_in"].append(d.name)
                self._send_json(list(lib.values()))
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
        elif route == "/api/assets/catalog":
            # §25 Asset Graph 台账（Library 级；含 hash/commercial/溯源字段）
            try:
                from video_director import asset_catalog as vd_acat
                qs = parse_qs(parsed.query)
                self._send_json(vd_acat.list_assets(
                    kind=qs.get("type", [""])[0] or None,
                    source=qs.get("source", [""])[0] or None,
                    q=qs.get("q", [""])[0],
                    only_clear=qs.get("only_clear", [""])[0] == "1"))
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
                # 素材元数据只读项目 manifest，不在 composition 请求中触发 ffmpeg。
                manifest_by_id = {}
                manifest_path = vd_config.project_dir(pid) / "assets" / "manifest.json"
                if manifest_path.is_file():
                    try:
                        for _a in json.loads(manifest_path.read_text(encoding="utf-8")):
                            if _a.get("id"):
                                manifest_by_id[_a["id"]] = _a
                    except (OSError, json.JSONDecodeError):
                        manifest_by_id = {}

                def _editor_asset_meta(asset_id):
                    _m = manifest_by_id.get(asset_id or "", {})
                    _f = _m.get("file") or ""
                    _safe_f = ""
                    if _f:
                        try:
                            _p = Path(_f).resolve()
                            _p.relative_to(STORAGE.resolve())
                            if _p.is_file():
                                _safe_f = str(_p)
                        except (OSError, ValueError):
                            _safe_f = ""
                    return {
                        "asset_file": _safe_f,
                        "asset_type": _m.get("type"),
                        "asset_duration": _m.get("duration"),
                        "asset_width": _m.get("width"),
                        "asset_height": _m.get("height"),
                        "asset_has_audio": _m.get("has_audio"),
                        "asset_source": _m.get("source"),
                        "asset_name": Path(_f).name if _f else "",
                    }
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
                                    **_editor_asset_meta(lay.asset_id),
                                    "motion_recipe_id": lay.motion_recipe_id,
                                    "motion_version": lay.motion_version,
                                    "locked": bool(getattr(lay, "locked", None) or False) or None,
                                    # video 层源区间/速度（§5.1 Inspector 裁剪与速度 UI 必需）
                                    "source_in": getattr(lay, "source_in", None),
                                    "source_out": getattr(lay, "source_out", None),
                                    "speed": getattr(lay, "speed", None),
                                    # 变换/动效实例/蒙版/关键帧（§5.1 变换、§6 蒙版、§7 关键帧 UI）
                                    "transform": (lay.transform and lay.transform.model_dump()) or None,
                                    "motion_instance": (lay.motion_instance and getattr(lay.motion_instance, "model_dump", lambda: lay.motion_instance)()) or None,
                                    "mask": (lay.mask and lay.mask.model_dump()) or None,
                                    "keyframes": [t.model_dump() for t in (lay.keyframes or [])],
                                } for i, lay in enumerate(sc.layers)
                            ],
                        } for sc in ir.scenes
                    ],
                    # §18/§19/§32：音频轨/水印/字幕样式回传（Inspector/时间线可视化）
                    "audio": [
                        {"type": t.type.value, "asset_id": t.asset_id, "start": t.start,
                         "duration": t.duration, "volume": t.volume, "ducking": t.ducking,
                         # §37 Audio Bus：muted/fade 前端可显（轨道 badge + Inspector）
                         "muted": bool(getattr(t, "muted", False)),
                         "fadeIn": getattr(t, "fadeIn", 0.0), "fadeOut": getattr(t, "fadeOut", 0.0)}
                        for t in ir.audio
                    ],
                    "watermarks": [
                        {"kind": w.kind, "text": w.text, "asset_id": w.asset_id,
                         "anchor": w.anchor, "opacity": w.opacity, "scale": w.scale}
                        for w in (ir.watermarks or [])
                    ],
                    "subtitle_style": ir.subtitle_style.model_dump() if ir.subtitle_style else None,
                    "references": ir.references.to_dict()["references"] if ir.references else [],
                    "revisions_count": len(revs),
                })
            except Exception as exc:
                self._send_json({"error": str(exc)}, 404)
        elif route == "/api/editor/material_match":
            # AI 导演素材匹配：只生成建议，不直接修改 Composition。
            qs = parse_qs(parsed.query)
            pid = (qs.get("project_id") or [""])[0]
            if not pid:
                self._send_json({"error": "缺少 project_id"}, 400)
                return
            try:
                from video_director import editor as vd_editor
                from video_director import matcher as vd_matcher
                from video_director import llm_provider as vd_llm
                from video_director.models import AssetRef

                ir = vd_editor.load_composition(pid)
                manifest_path = vd_config.project_dir(pid) / "assets" / "manifest.json"
                if not manifest_path.is_file():
                    self._send_json({"ok": True, "matches": [], "unmatched": [s.id for s in ir.scenes],
                                     "message": "项目没有素材 manifest"}, 200)
                    return
                raw_assets = json.loads(manifest_path.read_text(encoding="utf-8"))
                assets = {}
                for raw in raw_assets:
                    if raw.get("type") != "video" or not raw.get("id") or not raw.get("file"):
                        continue
                    try:
                        assets[raw["id"]] = AssetRef.model_validate(raw)
                    except Exception:
                        continue

                scenes = [{"id": s.id, "text": s.text or ""} for s in ir.scenes]
                assignments = {}
                used = set()
                llm = vd_llm.get_llm()
                if llm is not None:
                    try:
                        assignments = vd_matcher.llm_match(scenes, assets, llm.generate)
                        used = set(assignments.values())
                    except Exception as exc:
                        logger.warning(f"editor material match LLM failed, fallback deterministic: {exc}")
                        assignments = {}
                        used = set()

                for s in scenes:
                    if s["id"] in assignments:
                        continue
                    aid = vd_matcher.deterministic_match(
                        s["text"], s["id"], assets, used
                    )
                    if aid:
                        assignments[s["id"]] = aid
                        used.add(aid)

                matches = []
                unmatched = []
                for sc in ir.scenes:
                    aid = assignments.get(sc.id)
                    if not aid:
                        unmatched.append(sc.id)
                        continue
                    layer_idx = next(
                        (i for i, l in enumerate(sc.layers) if l.type.value == "video"),
                        None,
                    )
                    if layer_idx is None:
                        unmatched.append(sc.id)
                        continue
                    asset = assets.get(aid)
                    if asset is None:
                        unmatched.append(sc.id)
                        continue
                    lay = sc.layers[layer_idx]
                    source_in = float(getattr(lay, "source_in", 0.0) or 0.0)
                    source_out = getattr(lay, "source_out", None)
                    if source_out is not None:
                        source_out = float(source_out)
                    matches.append({
                        "scene_id": sc.id,
                        "scene_text": sc.text or "",
                        "layer_target": f"{sc.id}#{layer_idx}",
                        "asset_id": aid,
                        "asset_name": Path(asset.file).name,
                        "asset_file": asset.file,
                        "asset_duration": asset.duration,
                        "source_in": source_in,
                        "source_out": source_out,
                    })
                self._send_json({
                    "ok": True,
                    "matches": matches,
                    "unmatched": unmatched,
                    "strategy": "llm_then_deterministic" if llm is not None else "deterministic",
                })
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
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
        elif route == "/api/actions":
            # §62 Action Manifest API：按上下文返回可执行动作 + Availability。
            #   无 selection → 全上下文分组 manifest（UI 渲染按钮 / Director 读面）
            #   有 selection → 当前上下文动作组（每个动作带 available/reason/next_actions）
            # §42 build_id：源码契约与运行态一致性（Developer Mode 显示）。
            try:
                from video_director import action_registry as vd_actions
                qs = parse_qs(parsed.query)
                pid = qs.get("project_id", [""])[0]
                sel_type = qs.get("selection_type", [""])[0]
                layer_type = qs.get("layer_type", [""])[0]
                context = None
                if pid:
                    from video_director import project_context as vd_pctx
                    selection = ({"type": sel_type, "id": qs.get("selection_id", [""])[0],
                                  "layer_type": layer_type} if sel_type else None)
                    context = vd_pctx.inspect_project_context(pid, selection)
                if sel_type:
                    self._send_json(vd_actions.actions_for_context(
                        {"type": sel_type, "id": qs.get("selection_id", [""])[0],
                         "layer_type": layer_type or None},
                        project_id=pid or None, context=context))
                else:
                    self._send_json({
                        "build_id": vd_actions.BUILD_ID,
                        "system": vd_actions.system_context(),
                        "groups": vd_actions.manifest_for_project(pid or None),
                    })
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
        elif route == "/api/workflows":
            # §52 GET /api/workflows：run 列表（可选 project_id 过滤）
            try:
                from video_director import workflow_run as vd_wf
                qs = parse_qs(parsed.query)
                pid = qs.get("project_id", [""])[0]
                self._send_json({"runs": vd_wf.list_runs(project_id=pid or None)})
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
        elif route.startswith("/api/workflows/"):
            # §52 GET /api/workflows/{run_id}：run 详情 + steps[]（持久化状态）
            try:
                from video_director import workflow_run as vd_wf
                run_id = route[len("/api/workflows/"):].strip("/")
                run = vd_wf.get_run(run_id)
                if run is None:
                    self._send_json({"error": f"run 不存在: {run_id}"}, 404)
                    return
                self._send_json(run)
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
        elif route == "/api/director/failure":
            # FAILED 项目「查看问题」：失败 run 详情 + director_plan 失败节点
            # （不进编辑器——editable 门禁会把无 composition 的失败项目挡死）。
            qs = parse_qs(parsed.query)
            pid = (qs.get("project_id", [""])[0] or "").strip()
            if not pid:
                self._send_json({"ok": False, "error": "缺少 project_id"}, 400)
                return
            try:
                from video_director import workflow_run as vd_wf
                runs = vd_wf.list_runs(project_id=pid, limit=10)
                failed = [r for r in runs if r.get("status") in ("FAILED", "INTERRUPTED")]
                latest = (failed or runs)[0] if (failed or runs) else None
                detail = vd_wf.get_run(latest["run_id"]) if latest else {}
                failed_nodes = []
                plan_path = vd_config.project_dir(pid) / "plans" / "director_plan.json"
                if plan_path.is_file():
                    try:
                        plan = json.loads(plan_path.read_text(encoding="utf-8"))
                        failed_nodes = [
                            {"id": n.get("id"), "error": (n.get("error") or "")[:300]}
                            for n in plan.get("nodes") or []
                            if n.get("status") == "FAILED"
                        ]
                    except (OSError, json.JSONDecodeError):
                        pass
                self._send_json({
                    "ok": True,
                    "project_id": pid,
                    "run_id": (latest or {}).get("run_id", ""),
                    "status": (latest or {}).get("status", ""),
                    "error": (detail.get("error") or (latest or {}).get("error") or "")[:500],
                    "failed_nodes": failed_nodes,
                    "can_retry": bool(not latest or latest.get("status") != "RUNNING"),
                })
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
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

        # §52 POST /api/workflows：发起 WorkflowRun → 立即返回 run_id（不阻塞）。
        #   kind: compose（spec 同 /api/compose 字段）| render_draft | render_final
        #   前端轮询 GET /api/workflows/{run_id} 或走 SSE workflow.* 事件。
        if parsed.path == "/api/workflows":
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON（需 UTF-8）"}, 400)
                return
            try:
                from video_director import workflow_run as vd_wf
                kind = payload.get("kind", "")
                if kind == "compose":
                    spec = payload.get("spec") or {}
                    if not (spec.get("script") or "").strip():
                        self._send_json({"error": "请先粘贴文案"}, 400)
                        return
                    run_id = vd_wf.run_compose_async(spec)
                elif kind in ("render_draft", "render_final"):
                    pid = payload.get("project_id", "")
                    if not pid:
                        self._send_json({"error": "缺少 project_id"}, 400)
                        return
                    run_id = vd_wf.run_render_async(
                        pid, "draft" if kind == "render_draft" else "final")
                else:
                    self._send_json({"error": f"未知工作流类型: {kind}"}, 400)
                    return
                self._send_json({"ok": True, "run_id": run_id})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        if parsed.path == "/api/voice_preview_full":
            # §35 完整旁白试听：POST {voice, text} → edge-tts 合成整段（支持回放）
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON"}, 400)
                return
            voice = (payload.get("voice") or "").strip()
            text = (payload.get("text") or "").strip()
            if not voice or not text:
                self._send_json({"error": "缺少 voice 或 text"}, 400)
                return
            if len(text) > 1200:
                text = text[:1200]  # 试听上限（防超长阻塞）
            try:
                import asyncio
                import edge_tts
                import hashlib as _hl
                cache_dir = COMPOSER_ROOT / "voice_previews"
                cache_dir.mkdir(parents=True, exist_ok=True)
                key = _hl.sha1(f"{voice}:{text}".encode("utf-8")).hexdigest()[:16]
                cache_file = cache_dir / f"full-{key}.mp3"
                if not (cache_file.is_file() and cache_file.stat().st_size > 0):
                    async def _syn():
                        com = edge_tts.Communicate(text, voice)
                        await com.save(str(cache_file))
                    asyncio.run(_syn())
                self._send_json({"ok": True,
                                  "audio_url": "/file?path=" + urllib_quote(str(cache_file))})
            except Exception as exc:
                self._send_json({"ok": False, "error": f"完整试听合成失败: {exc}"}, 502)
            return

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
                # 素材落盘（系列共享素材目录；视频/图片均可作为共享输入）
                video_paths, image_paths = [], []
                shared_dir = COMPOSER_ROOT / f"series-{series_id[:12]}" / "input" / "shared"
                shared_dir.mkdir(parents=True, exist_ok=True)
                for f in files:
                    if f["name"] not in ("videos", "images"):
                        continue
                    dst = shared_dir / Path(f["filename"]).name
                    dst.write_bytes(f["data"])
                    if f["name"] == "videos":
                        video_paths.append(str(dst))
                    else:
                        image_paths.append(str(dst))
                job_ids = vd_studio.enqueue_series_batch(
                    series_id, spec, episodes, video_paths, image_paths)
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

        # §24/§50 AssetPicker 本地导入 tab：multipart 文件 → 项目 assets manifest
        # §26 Asset Library 上传：文件 → 安检 → probe → hash → Library（项目无关，
        # 同一素材多项目复用）。项目引用由 /api/assets_import 或 project_reference 建。
        # §26/§52 素材暂存：WorkflowRun spec 走 JSON（不能嵌文件），上传文件
        # 先落 storage/staging → 返回路径列表供 spec.video_paths/image_paths 引用。
        if parsed.path == "/api/assets/stage":
            mp = _parse_multipart(self)
            files = [f for f in mp["files"] if f["name"] == "files"]
            if not files:
                self._send_json({"ok": False, "error": "请选择文件"}, 400)
                return
            from video_director import assets as vd_assets_mod
            stage_dir = STORAGE / "staging"
            stage_dir.mkdir(parents=True, exist_ok=True)
            videos, images, audio, errs = [], [], [], []
            for f in files:
                name = Path(f["filename"]).name
                dst = stage_dir / name
                dst.write_bytes(f["data"])
                try:
                    kind = vd_assets_mod.classify(str(dst))
                    if kind == "audio":
                        audio.append(str(dst))
                    else:
                        (videos if kind == "video" else images).append(str(dst))
                except vd_assets_mod.AssetError:
                    dst.unlink(missing_ok=True)
                    errs.append(f"{name}: 不支持的类型")
            self._send_json({"ok": bool(videos or images or audio),
                             "videos": videos, "images": images, "audio": audio, "errors": errs})
            return

        if parsed.path == "/api/assets/upload":
            mp = _parse_multipart(self)
            fields, files = mp["fields"], mp["files"]
            srcs = [f for f in files if f["name"] == "files"]
            if not srcs:
                self._send_json({"error": "请选择文件"}, 400)
                return
            from video_director import asset_catalog as vd_acat
            import tempfile as _tempfile
            imported, errors = [], []
            for f in srcs:
                # 先落临时文件（import_asset 需要 path 做 magic/probe/hash）
                suffix = Path(f["filename"]).suffix.lower()
                with _tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
                    tf.write(f["data"])
                    tmp_path = tf.name
                try:
                    tags = [t.strip() for t in (fields.get("tags") or "").split(",") if t.strip()]
                    entry = vd_acat.import_asset(
                        tmp_path, source=fields.get("source") or "user",
                        tags=tags, description=fields.get("description") or "")
                    imported.append({
                        "asset_id": entry["asset_id"], "type": entry["type"],
                        "file": entry["file"],
                        "width": entry.get("width"), "height": entry.get("height"),
                        "duration": entry.get("duration"), "codec": entry.get("codec"),
                        "has_audio": entry.get("has_audio"),
                        "orientation": entry.get("orientation"),
                        "commercial_status": entry.get("commercial_status"),
                    })
                except vd_acat.AssetLibraryError as exc:
                    errors.append(f"{Path(f['filename']).name}: {exc}")
                except Exception as exc:  # noqa: BLE001 单文件失败不阻断整批
                    errors.append(f"{Path(f['filename']).name}: 导入失败 {exc}")
                finally:
                    Path(tmp_path).unlink(missing_ok=True)
            sse_publish("asset", {"phase": "library_uploaded", "count": len(imported)})
            self._send_json({"ok": bool(imported), "imported": imported,
                             "errors": errors,
                             "error": "" if imported else (errors[0] if errors else "无文件")})
            return

        if parsed.path == "/api/assets_import":
            mp = _parse_multipart(self)
            fields, files = mp["fields"], mp["files"]
            project_id = (fields.get("project_id") or "").strip()
            if not project_id:
                self._send_json({"error": "缺少 project_id（先打开项目）"}, 400)
                return
            vids = [f for f in files if f["name"] == "files"]
            if not vids:
                self._send_json({"error": "请选择文件"}, 400)
                return
            try:
                base = vd_config.project_dir(project_id)
                in_dir = base / "input" / "videos"
                in_dir.mkdir(parents=True, exist_ok=True)
                from video_director import assets as vd_assets_mod
                video_paths, image_paths = [], []
                asset_ids = []
                for f in vids:
                    dst = in_dir / Path(f["filename"]).name
                    dst.write_bytes(f["data"])
                    try:
                        kind = vd_assets_mod.classify(str(dst))
                    except vd_assets_mod.AssetError:
                        dst.unlink(missing_ok=True)
                        continue
                    if kind == "video":
                        video_paths.append(str(dst))
                    else:
                        image_paths.append(str(dst))
                # 探测并入项目 manifest（build_manifest + 合并已有）
                if video_paths or image_paths:
                    new_assets, _order = vd_assets_mod.build_manifest(
                        video_paths, image_paths,
                        license_note="user-imported-from-picker")
                    manifest_path = base / "assets" / "manifest.json"
                    existing = []
                    if manifest_path.is_file():
                        try:
                            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
                        except (OSError, json.JSONDecodeError):
                            existing = []
                    have = {a["id"] for a in existing}
                    for k, ref in new_assets.items():
                        if k not in have:
                            existing.append(json.loads(ref.model_dump_json()))
                            asset_ids.append(k)
                    manifest_path.parent.mkdir(parents=True, exist_ok=True)
                    manifest_path.write_text(
                        json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
                # §48 SSE：素材导入事件
                sse_publish("asset", {"phase": "imported", "project_id": project_id,
                                     "count": len(asset_ids)})
                self._send_json({"ok": True, "imported": len(asset_ids),
                                 "asset_ids": asset_ids})
            except Exception as exc:
                self._send_json({"ok": False, "error": f"导入失败: {exc}"}, 500)
            return

        # §16 上传配音（Import→Probe→ASR→Word Timing→Narration 轨，TTS=SKIP）
        if parsed.path == "/api/voice_upload":
            mp = _parse_multipart(self)
            fields, files = mp["fields"], mp["files"]
            project_id = (fields.get("project_id") or "").strip()
            if not project_id:
                self._send_json({"error": "缺少 project_id（先创作或打开项目）"}, 400)
                return
            up = [f for f in files if f["name"] == "voice_file"]
            if not up:
                self._send_json({"error": "请选择配音音频文件"}, 400)
                return
            f = up[0]
            ext = Path(f["filename"]).suffix.lower()
            ALLOWED_V = (".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma")
            if ext not in ALLOWED_V:
                self._send_json({"error": f"不支持的格式 {ext}，允许：{'/'.join(ALLOWED_V)}"}, 400)
                return
            if len(f["data"]) > 30 * 1024 * 1024:
                self._send_json({"error": "文件超过 30MB 上限"}, 400)
                return
            # 场景文本（可选）：项目已有分镜则按其文本对齐，无则整段
            scene_texts = []
            try:
                sb_file = vd_config.project_dir(project_id) / "plans" / "scenes.json"
                if sb_file.is_file():
                    for s in json.loads(sb_file.read_text(encoding="utf-8")):
                        if s.get("text"):
                            scene_texts.append(s["text"])
            except Exception:
                scene_texts = []
            try:
                tmp = vd_config.project_dir(project_id) / "input"
                tmp.mkdir(parents=True, exist_ok=True)
                tmp_file = tmp / f"voice_upload{ext}"
                tmp_file.write_bytes(f["data"])
                from video_director import voice_upload as vd_vu
                out = vd_vu.import_uploaded_voice(
                    project_id, str(tmp_file),
                    scene_texts=scene_texts or None)
                # §48 SSE：配音导入完成事件
                sse_publish("voice", {"phase": "uploaded", "project_id": project_id,
                                      "source": out["source"],
                                      "duration": out["duration"]})
                self._send_json({"ok": True, **out})
            except vd_vu.VoiceUploadError as exc:
                self._send_json({"ok": False, "error": str(exc), "code": exc.code}, 400)
            except Exception as exc:
                self._send_json({"ok": False, "error": f"配音导入失败: {exc}"}, 500)
            return

        # 多模型配置中心（四角色独立配置，整体保存）
        if parsed.path == "/api/settings/import":
            # §52 配置导入：结构与模型名——不动本机已有密钥（角色保存时 __KEEP__）
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "导入文件不是合法 JSON"}, 400)
                return
            models = payload.get("models")
            if not isinstance(models, list):
                self._send_json({"error": "导入文件缺 models 列表"}, 400)
                return
            try:
                existing = vd_model_config.load_all()
                configs = {}
                for m in models:
                    if not isinstance(m, dict) or not m.get("role"):
                        continue
                    old = existing.get(m["role"]) or {}
                    configs[m["role"]] = {
                        "api_key": "__KEEP__",  # 导入不覆盖密钥（§52 安全边界）
                        "base_url": m.get("base_url") or old.get("base_url", ""),
                        "model": m.get("model") or old.get("model", ""),
                    }
                if configs:
                    vd_model_config.save_all(configs)
                    vd_model_config.sync_llm_provider()
                self._send_json({"ok": True,
                                 "imported_roles": sorted(configs.keys()),
                                 "roles": vd_model_config.status_for_frontend()})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

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

        # 主题 → Script：创建页生成文案
        if parsed.path == "/api/script/generate":
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON"}, 400)
                return
            topic = (payload.get("topic") or "").strip()
            if not topic:
                self._send_json({"error": "主题不能为空"}, 400)
                return
            try:
                from video_director import llm_provider as _lp
                script = _lp.generate_script(
                    topic,
                    platform=(payload.get("platform") or "").strip(),
                    duration=str(payload.get("duration") or "").strip(),
                    style=(payload.get("style") or "").strip(),
                )
                self._send_json({"ok": True, "script": script})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 503)
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
        if parsed.path == "/api/director/context":
            # §23/§46 Command Bar 上下文：POST 上报前端 Selection（Director
            # execute 时并入 project_context 快照）；GET 读快照。
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON"}, 400)
                return
            pid = (payload.get("project_id") or "").strip()
            selection = payload.get("selection") or {}
            if not pid:
                self._send_json({"error": "缺少 project_id"}, 400)
                return
            _DIRECTOR_SELECTION[pid] = selection  # 内存态（单机单用户；重启即清）
            try:
                from video_director import project_context
                self._send_json({"ok": True,
                    "context": project_context.inspect_project_context(pid, selection)})
            except Exception as exc:
                self._send_json({"ok": True, "context": {}})  # 上报成功即可，快照失败不阻塞
            return

        # AI Director：所有创作来源统一进入 DirectorInputBundle（无需先建项目）。
        if parsed.path == "/api/director/create":
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(
                    self.rfile.read(length).decode("utf-8", errors="replace") or "{}"
                )
            except json.JSONDecodeError:
                self._send_json({"ok": False, "error": "请求体不是合法 JSON"}, 400)
                return

            inputs = payload.get("director_inputs") or {}
            brief = (
                (inputs.get("brief") if isinstance(inputs, dict) else "")
                or payload.get("brief")
                or payload.get("creative_brief")
                or ""
            ).strip()
            script = (
                (inputs.get("script", {}).get("text")
                 if isinstance(inputs.get("script"), dict) else inputs.get("script"))
                or payload.get("script")
                or ""
            ).strip()
            assets = (inputs.get("assets") or {}) if isinstance(inputs, dict) else {}
            has_sources = bool(
                script
                or (assets.get("asset_refs") if isinstance(assets, dict) else [])
                or (inputs.get("references") if isinstance(inputs, dict) else [])
                or (inputs.get("software_project") if isinstance(inputs, dict) else {})
            )
            if not brief and not script and not has_sources:
                self._send_json({
                    "ok": False,
                    "error": "请告诉 AI 想做什么，并可同时附加文案、素材、参考视频或软件项目",
                }, 400)
                return

            try:
                from video_director import director_inputs as _di
                from video_director import workflow_run as _wf
                from video_director import config as _cfg

                pid = (
                    payload.get("project_id") or
                    (inputs.get("project_id") if isinstance(inputs, dict) else "") or
                    f"web-{uuid.uuid4().hex[:8]}"
                )
                merged = dict(payload)
                merged["project_id"] = pid
                merged["brief"] = brief
                merged["script"] = script
                merged["director_inputs"] = dict(inputs)
                merged["director_inputs"]["brief"] = brief
                merged["director_inputs"]["script"] = {
                    "text": script,
                    "source": "user" if script else "director",
                    "authority": "authoritative" if script else "generated",
                    "editable": True,
                }

                # Settings belong to the same input bundle, not a separate creation mode.
                merged["director_inputs"]["platform"] = (
                    inputs.get("platform") if isinstance(inputs, dict) else ""
                ) or payload.get("platform") or ""
                merged["director_inputs"]["duration"] = (
                    inputs.get("duration") if isinstance(inputs, dict) else ""
                ) or payload.get("duration") or ""
                merged["director_inputs"]["style"] = (
                    inputs.get("style") if isinstance(inputs, dict) else ""
                ) or payload.get("style") or payload.get("style_intent") or ""

                bundle = _di.build_bundle(merged)
                plan = _di.build_plan(bundle, brief or script or "AI Director 创作")

                resolution = str(payload.get("resolution") or "").strip()
                platform = str(payload.get("platform") or "").strip()
                platform_dims = {
                    "douyin": (1080, 1920), "video_channel": (1080, 1920),
                    "bilibili": (1920, 1080), "xhs": (1080, 1440),
                    "square": (1080, 1080),
                }
                if resolution and "x" in resolution:
                    rw, rh = [int(x) for x in resolution.split("x", 1)]
                else:
                    rw, rh = platform_dims.get(platform, _cfg.DEFAULT_RESOLUTION)

                confirm_mode = (payload.get("confirm_mode") or "preview").strip()
                spec = {
                    "project_id": pid,
                    "script": script,
                    "creative_brief": brief,
                    "brief": brief,
                    "mode": payload.get("mode") or "auto",
                    "voice": payload.get("voice") or "zh-CN-XiaoxiaoNeural",
                    "voice_mode": payload.get("voice_mode") or "auto",
                    "voice_rate": payload.get("voice_rate") or "1.0",
                    "style_intent": payload.get("style_intent") or "",
                    "width": rw, "height": rh,
                    "fps": int(payload.get("fps") or 30),
                    "bgm": payload.get("bgm") or "auto",
                    "bgm_volume": float(payload.get("bgm_volume") or 0.3),
                    "narration_volume": float(payload.get("narration_volume") or 1.0),
                    "sfx_plan": payload.get("sfx_plan"),
                    "subtitle_mode": payload.get("subtitle_mode") or "plain",
                    "fit_mode": payload.get("fit_mode") or "cover",
                    "clip_duration": payload.get("clip_duration"),
                    "clip_speed": payload.get("clip_speed"),
                    "concat_mode": payload.get("concat_mode") or "sequential",
                    "transition_mode": payload.get("transition_mode") or "cut",
                    "match_materials_to_script": bool(payload.get("match_materials_to_script", True)),
                    "watermark": payload.get("watermark") or "",
                    "lower_third_name": payload.get("lower_third_name") or "",
                    "lower_third_role": payload.get("lower_third_role") or "",
                    "confirm_mode": confirm_mode,
                    "auto_confirm": confirm_mode == "direct",
                    "visual_source": payload.get("visual_source") or "auto",
                    "video_paths": payload.get("video_paths") or [],
                    "image_paths": payload.get("image_paths") or [],
                    "narration_upload": payload.get("narration_upload") or "",
                    "director_inputs": bundle,
                }
                run_id = _wf.run_director_create_async(spec, plan=plan)
                self._send_json({
                    "ok": True,
                    "run_id": run_id,
                    "project_id": pid,
                    "script": script,
                    "director_plan": plan,
                    "director_inputs": bundle,
                })
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 503)
            return

        if parsed.path == "/api/director/retry":
            # FAILED 导演项目「重新执行」：读持久化 bundle/params → 新 director_create run。
            # 门禁在 workflow_run.run_director_retry_async（项目存在/有 bundle/无 RUNNING run）。
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(
                    self.rfile.read(length).decode("utf-8", errors="replace") or "{}"
                )
            except json.JSONDecodeError:
                self._send_json({"ok": False, "error": "请求体不是合法 JSON"}, 400)
                return
            pid = (payload.get("project_id") or "").strip()
            if not pid:
                self._send_json({"ok": False, "error": "缺少 project_id"}, 400)
                return
            try:
                from video_director import workflow_run as _wf
                run_id = _wf.run_director_retry_async(pid)
                if not run_id:
                    # 区分三种拒绝原因，给用户可解释错误
                    from video_director import store as _vd_store
                    if _vd_store.get_project(pid) is None:
                        reason = "项目不存在"
                    elif not (vd_config.project_dir(pid) / "plans" / "director_inputs.json").is_file():
                        reason = "该项目没有 AI 导演输入记录（可能是旧流程项目），无法重新执行"
                    else:
                        reason = "项目有正在执行的任务，结束后再试"
                    self._send_json({"ok": False, "error": reason}, 409)
                    return
                self._send_json({"ok": True, "run_id": run_id, "project_id": pid})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        # §12 UI/AI 共用唯一动作入口：Action → Capability → Command Bus。
        # UI 只提交用户语义 action；Director 也使用同一 Registry。
        if parsed.path == "/api/action/execute":
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"ok": False, "status": "REJECTED", "code": "INVALID_JSON", "error": "请求体不是合法 JSON"}, 400)
                return
            pid = (payload.get("project_id") or "").strip()
            action_id = (payload.get("action") or "").strip()
            target = payload.get("target") or "current_selection"
            params = payload.get("params") or {}
            selection = payload.get("selection")
            if not selection:
                selection = _DIRECTOR_SELECTION.get(pid) or {"type": "project"}
            if not pid or not action_id:
                self._send_json({"ok": False, "status": "REJECTED", "code": "MISSING_REQUIREMENT", "error": "缺少 project_id 或 action"}, 400)
                return
            try:
                from video_director import action_registry as vd_actions
                from video_director import project_context as vd_pctx
                from video_director import tool_executor as vd_tools
                context = vd_pctx.inspect_project_context(pid, selection)
                availability = vd_actions.action_availability(action_id, pid, context=context)
                if not availability.get("available"):
                    self._send_json({"ok": False, "status": "REJECTED", "code": "ACTION_UNAVAILABLE", "action": action_id, "reason": availability.get("reason") or "当前对象不可执行该操作", "next_actions": availability.get("next_actions") or []}, 409)
                    return
                resolved = vd_actions.resolve_step({"action": action_id, "target": target, "params": params}, selection=selection)
                if resolved.get("error"):
                    self._send_json({"ok": False, "status": "REJECTED", "code": "ACTION_RESOLVE_FAILED", "action": action_id, "error": resolved["error"]}, 400)
                    return
                spec = vd_actions.get_action(resolved["action"])
                sel_type = (selection or {}).get("type") or "project"
                target_type = "project" if target == "project" or not resolved.get("targetId") else sel_type
                if spec and spec.targets and target_type not in spec.targets:
                    self._send_json({"ok": False, "status": "REJECTED", "code": "TARGET_NOT_ALLOWED", "action": resolved["action"], "error": "动作 %s 不允许目标 %s" % (resolved["action"], target_type)}, 409)
                    return
                if resolved["action"] == "render.final":
                    from video_director import preflight as vd_preflight
                    preflight = vd_preflight.run_preflight(pid, for_publish=True)
                    if preflight.status != "PASS":
                        self._send_json({"ok": False, "status": "REJECTED", "code": "PREFLIGHT_REJECT", "action": resolved["action"], "reason": "商用预检未通过", "preflight": preflight.to_dict()}, 409)
                        return
                result = vd_tools.execute_tool(pid, {"tool": resolved["capability"], "targetId": resolved.get("targetId") or "", "payload": resolved.get("payload") or {}})
                ok = bool(result.get("ok"))
                self._send_json({"ok": ok, "status": result.get("status") or ("APPLIED" if ok else "REJECTED"), "action": resolved["action"], "capability": resolved["capability"], "command": resolved["command"], "revision_id": result.get("revision_id"), "composition_version": result.get("composition_version"), "result": result.get("result") or {}, "verify": result.get("verify") or {}, "next_actions": [] if ok else (availability.get("next_actions") or []), "error": result.get("error"), "message": spec.label if ok and spec else ""}, 200)
            except Exception as exc:
                self._send_json({"ok": False, "status": "ERROR", "code": "ACTION_EXECUTE_FAILED", "action": action_id, "error": str(exc)}, 500)
            return
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
                # §48 SSE：Director 过程事件推送（规划开始 → 逐工具 → 完成）
                sse_publish("director", {"phase": "planning", "project_id": pid,
                                         "instruction": instruction[:60]})
                # §46 前端 Selection 并入指令（Command Bar 上下文：「正在修改 Scene 03」
                # 时指令自动带目标对象，Director 可据此缩小作用域）
                sel_ctx = _DIRECTOR_SELECTION.get(pid)
                instruction_with_ctx = instruction
                if sel_ctx and sel_ctx.get("type") not in (None, "", "none", "project"):
                    instruction_with_ctx = f"[当前选中：{sel_ctx.get('type')} {sel_ctx.get('id')}] {instruction}"
                result = vd_director.direct(pid, instruction, selection=sel_ctx)
                for step in result.get("executed", []):
                    sse_publish("director", {
                        "phase": "tool", "project_id": pid, "tool": step.get("tool"),
                        "ok": step.get("ok"), "status": step.get("status")})
                sse_publish("director", {"phase": "done", "project_id": pid,
                                         "verify_ok": result.get("verify_ok")})
                self._send_json({"ok": True, "director": result})
            except vd_director.DirectorError as exc:
                sse_publish("director", {"phase": "error", "project_id": pid,
                                         "code": exc.code})
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
                from video_director import asset_catalog as vd_acat
                # §10 ratio 由当前 Project Context 决定（不硬编码 9:16）：
                # payload.ratio > project_id 推导 > any
                ratio = payload.get("ratio")
                pid_for_ratio = payload.get("project_id") or ""
                orientation = payload.get("orientation")
                if not ratio and pid_for_ratio:
                    sp = vd_acat.stock_search_params(pid_for_ratio)
                    ratio = sp["ratio"]
                    if not orientation:
                        orientation = sp["orientation"]
                hits = vd_stock.search_stock(
                    query, provider=payload.get("provider"),
                    orientation=orientation)
                cands = vd_stock.resolve_candidates(
                    hits, target_ratio=ratio or "any",
                    target_duration=payload.get("targetDuration"))
                self._send_json({"ok": True,
                                 "candidates": [c.to_dict() for c in cands]})
            except vd_stock.StockError as exc:
                self._send_json({"ok": False, "error": str(exc), "code": exc.code}, 503)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        # Stock 下载采用（§24：联网素材 → 下载到项目输入目录 → 进时间线）
        if parsed.path == "/api/stock/download":
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON（需 UTF-8）"}, 400)
                return
            pid = (payload.get("project_id") or "").strip()
            a = payload.get("asset") or {}
            if not pid or not a.get("media_url"):
                self._send_json({"error": "缺少 project_id 或 asset.media_url"}, 400)
                return
            try:
                from video_director import stock as vd_stock
                asset = vd_stock.StockAsset(
                    provider=a.get("provider", ""), source_id=a.get("sourceId", ""),
                    source_page=a.get("sourcePage", ""),
                    media_url=(a.get("mediaUrl") or a.get("media_url", "")),
                    width=int(a.get("width") or 0), height=int(a.get("height") or 0),
                    duration=float(a.get("duration") or 0),
                    creator=a.get("creator", ""), description=a.get("visualDescription", ""))
                base = vd_config.project_dir(pid)
                out = vd_stock.download_asset(asset, base)
                # §24：下载后注册进项目 manifest（build_manifest 探测 + 合并），
                # 返回本地 asset_id，前端才能 replace_source 采用
                try:
                    from video_director import assets as vd_assets_mod
                    if out["type"] == "video":
                        new_assets, _ = vd_assets_mod.build_manifest(
                            [out["file"]], [], license_note=f"stock-{asset.provider}")
                    else:
                        new_assets, _ = vd_assets_mod.build_manifest(
                            [], [out["file"]], license_note=f"stock-{asset.provider}")
                    manifest_path = base / "assets" / "manifest.json"
                    existing = []
                    if manifest_path.is_file():
                        try:
                            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
                        except (OSError, json.JSONDecodeError):
                            existing = []
                    have = {a["id"] for a in existing}
                    local_id = None
                    for k, ref in new_assets.items():
                        if k not in have:
                            existing.append(json.loads(ref.model_dump_json()))
                        local_id = k
                    manifest_path.parent.mkdir(parents=True, exist_ok=True)
                    manifest_path.write_text(
                        json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
                    if local_id:
                        out["asset_id"] = local_id
                    # §31/§39 Stock 素材入 Asset Registry（Library 级台账：
                    # provider/creator/attribution 可追溯，多项目复用）
                    try:
                        from video_director import asset_catalog as vd_acat2
                        vd_acat2.register_stock(
                            out["file"], provider=asset.provider,
                            source_id=asset.source_id, source_page=asset.source_page,
                            creator=asset.creator)
                    except Exception:  # noqa: BLE001 台账失败不阻断下载链
                        pass
                except Exception as exc:
                    print(f"[web] stock download 注册 manifest 失败（不阻塞下载）: {exc}",
                          file=sys.stderr)
                self._send_json({"ok": True, **out})
            except vd_stock.StockError as exc:
                self._send_json({"ok": False, "error": str(exc), "code": exc.code}, 502)
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
                # §四 WorkflowRun=运行态事实源：同步渲染也必须登记 RUNNING run，
                # 否则渲染中项目查不到 active_job（mid-render 可删）。
                run_id = _run_confirm_as_workflow(pid)
                try:
                    final = confirm_mod.finalize_after_review(pid, edits=edits)
                except Exception:
                    from video_director import workflow_run as vd_wf_fail
                    try:
                        vd_wf_fail.finish_run(run_id, vd_wf_fail.FAILED,
                                               error="确认渲染失败")
                    except Exception:  # noqa: BLE001 run 收尾失败不掩盖原错误
                        pass
                    raise
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
                from video_director import workflow_run as vd_wf_ok
                try:
                    vd_wf_ok.finish_run(run_id, vd_wf_ok.COMPLETED, result={
                        "project_id": pid, "final_path": str(final.final_path)})
                except Exception:  # noqa: BLE001 run 收尾失败不阻塞响应
                    pass
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
                custom_dir = COMPOSER_ROOT
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

        if parsed.path == "/api/bulk_action":
            # 统一批量治理入口：projects/inbox/workflow_runs/jobs/series/assets。
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"ok": False, "code": "INVALID_JSON", "error": "请求体不是合法 JSON"}, 400)
                return
            resource = (payload.get("resource") or "").strip()
            action = (payload.get("action") or "").strip()
            ids = payload.get("ids") or []
            if not isinstance(ids, list) or not resource or not action:
                self._send_json({"ok": False, "code": "INVALID_PAYLOAD", "error": "缺少 resource/action/ids"}, 400)
                return
            try:
                if resource == "projects":
                    from video_director import project_manager as vd_pm
                    if action == "delete":
                        result = vd_pm.bulk_delete(ids)
                    elif action == "archive":
                        result = vd_pm.bulk_archive(ids)
                    else:
                        self._send_json({"ok": False, "code": "UNSUPPORTED_ACTION", "error": "项目批量仅支持删除/归档"}, 400)
                        return
                elif resource == "inbox":
                    from video_director import studio as vd_studio
                    if action == "delete":
                        result = vd_studio.inbox_bulk_delete(ids)
                    elif action == "dismissed":
                        result = vd_studio.inbox_bulk_status(ids, "dismissed")
                    else:
                        self._send_json({"ok": False, "code": "UNSUPPORTED_ACTION", "error": "收集箱批量仅支持删除/丢弃"}, 400)
                        return
                elif resource == "workflow_runs":
                    from video_director import workflow_run as vd_wf
                    if action != "delete":
                        self._send_json({"ok": False, "code": "UNSUPPORTED_ACTION", "error": "任务批量仅支持清理"}, 400)
                        return
                    result = vd_wf.delete_runs(ids)
                elif resource == "jobs":
                    from video_director import studio as vd_studio
                    if action != "delete":
                        self._send_json({"ok": False, "code": "UNSUPPORTED_ACTION", "error": "队列批量仅支持清理"}, 400)
                        return
                    result = vd_studio.job_bulk_delete(ids)
                elif resource == "series":
                    from video_director import studio as vd_studio
                    if action != "delete":
                        self._send_json({"ok": False, "code": "UNSUPPORTED_ACTION", "error": "系列批量仅支持删除"}, 400)
                        return
                    result = vd_studio.series_bulk_delete(ids)
                elif resource == "assets":
                    from video_director import asset_catalog as vd_acat
                    if action != "delete":
                        self._send_json({"ok": False, "code": "UNSUPPORTED_ACTION", "error": "素材批量仅支持删除未使用素材"}, 400)
                        return
                    result = vd_acat.delete_assets(ids)
                else:
                    self._send_json({"ok": False, "code": "UNKNOWN_RESOURCE", "error": f"未知资源类型：{resource}"}, 400)
                    return
                self._send_json(result, 200 if result.get("ok") else 409)
            except Exception as exc:
                self._send_json({"ok": False, "code": "BULK_ACTION_FAILED", "error": str(exc)}, 500)
            return

        if parsed.path == "/api/project_action":
            # §6.1/§61 Project Action 统一动作接口：rename/clone/archive/restore/
            # delete/convert。全部实现收在 project_manager（server 不删文件系统）。
            # REJECT 类（ACTIVE_JOB 等）不是服务器错误 → HTTP 200 + ok:false 分流。
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"ok": False, "error": "请求体不是合法 JSON"}, 400)
                return
            pid = (payload.get("project_id") or "").strip()
            action = (payload.get("action") or "").strip()
            if not pid or not action:
                self._send_json({"ok": False, "error": "缺少 project_id 或 action"}, 400)
                return
            try:
                from video_director import project_manager as vd_pm
                from video_director.project_manager import ProjectActionError
                if action == "rename":
                    r = vd_pm.rename(pid, payload.get("new_name") or "")
                elif action == "clone":
                    r = vd_pm.clone(pid)
                elif action == "archive":
                    r = vd_pm.archive(pid)
                elif action == "restore":
                    r = vd_pm.restore(pid)
                elif action == "delete":
                    r = vd_pm.delete(pid)
                elif action == "convert":
                    r = vd_pm.legacy_convert(pid)
                else:
                    self._send_json({"ok": False, "error": f"未知操作：{action}"}, 400)
                    return
                self._send_json(r)
            except ProjectActionError as exc:
                self._send_json({"ok": False, "error": exc.message, "code": exc.code})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc), "code": "INTERNAL"}, 500)
            return

        if parsed.path == "/api/graphics/plan_to_project":
            # §7/FE-003 Graphic Plan → Composition 归一：proposal 确认 → 建项目
            # （合法 IR 骨架：每场景一个 graphic 层）→ 前端立即走
            # apply_graphic_plan Command 完善（此后修改全经 Command Bus）。
            # 禁止 GFX.plan → save 直通持久化：此端点只建骨架，正式数据经 Command。
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON"}, 400)
                return
            plan = payload.get("plan") or {}
            scenes = plan.get("scenes") or []
            script = (payload.get("script") or "").strip()
            if not scenes:
                self._send_json({"error": "缺少创作方案（先生成方案）"}, 400)
                return
            try:
                import uuid as _uuid
                from video_director.models import CompositionIR, SceneIR, Layer, LayerType
                pid = "gfx-" + _uuid.uuid4().hex[:8]
                base = vd_config.project_dir(pid)
                dirs = vd_config.init_project_dirs(base)
                [w, h] = (payload.get("size") or "1080x1920").split("x")
                fps = 30
                t0 = 0.0
                ir_scenes = []
                for sc in scenes:
                    dur = max(0.5, float(sc.get("duration") or 3.0))
                    gparams = {
                        "block_id": sc.get("block_id") or "kb.title",
                        "block_name": sc.get("block_name") or sc.get("block_id") or "",
                        "layout": sc.get("layout") or "center",
                        "source": sc.get("source") or "Generated Graphic",
                        "motion_id": sc.get("motion_id") or "graphics.fade-up",
                        "content_type": plan.get("content_type") or "knowledge",
                        "style_id": plan.get("style_id") or "editorial",
                        "narration": sc.get("narration") or sc.get("text") or "",
                        "visual_intent": sc.get("visual_intent") or "",
                    }
                    ir_scenes.append(SceneIR.model_validate({
                        "id": f"scene-{len(ir_scenes) + 1:02d}",
                        "start": t0, "duration": dur,
                        "text": gparams["narration"] or " ",
                        "visual_intent": gparams["visual_intent"],
                        "layers": [Layer(type=LayerType.graphic, start=0.0,
                                         duration=dur,
                                         motion_recipe_id=gparams["motion_id"],
                                         motion_instance={"parameters":
                                                          {"graphic": gparams}})],
                    }))
                    t0 += dur
                ir = CompositionIR.model_validate({
                    "project": {"title": script[:24] or "程序化创作",
                                "width": int(w), "height": int(h), "fps": fps,
                                "duration": t0},
                    "scenes": [json.loads(s.model_dump_json()) for s in ir_scenes],
                    "audio": [], "watermarks": [],
                })
                comp_path = dirs["plans"] / "composition.json"
                comp_path.write_text(json.dumps(json.loads(ir.model_dump_json()),
                                                ensure_ascii=False, indent=2),
                                     encoding="utf-8")
                (dirs["assets"] / "manifest.json").write_text("[]", encoding="utf-8")
                (dirs["input"] / "script.txt").write_text(script, encoding="utf-8")
                vd_store.create_project(pid, "[图形] " + (plan.get("content_type") or "knowledge"),
                                        {"mode": "graphic_workspace",
                                         "content_type": plan.get("content_type")})
                vd_store.set_stage(pid, vd_store.Stage.AWAITING_CONFIRM)
                self._send_json({"ok": True, "project_id": pid,
                                 "scene_count": len(ir.scenes), "duration": t0})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        if parsed.path in ("/api/promo/discover", "/api/promo/scan", "/api/promo/features"):
            # §65 Repo Promo UI：发现项目 → 扫描 → 功能证据（§34/§三十五~三十八）
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON"}, 400)
                return
            root = (payload.get("root") or "").strip()
            if not root or not Path(root).is_dir():
                self._send_json({"error": "请提供有效的开发项目根目录（不扫全盘）"}, 400)
                return
            try:
                from video_director import repo_promo as vd_repo_promo
                if parsed.path == "/api/promo/discover":
                    repos = vd_repo_promo.discover_repos(root)
                    self._send_json({"ok": True, "repos": repos})
                elif parsed.path == "/api/promo/scan":
                    snap = vd_repo_promo.scan_project(root)
                    self._send_json({"ok": True, "snapshot": snap})
                else:
                    graph = vd_repo_promo.discover_features(root)
                    self._send_json({"ok": True, "evidence_graph": graph.to_dict()})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        if parsed.path == "/api/editor/matting":
            # §64 抠像（Matte）：ChromaKey Provider → 抠像产物注册进项目素材库
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON"}, 400)
                return
            pid = (payload.get("project_id") or "").strip()
            target = (payload.get("target_id") or "").strip()
            if not pid or not target:
                self._send_json({"error": "缺少 project_id / target_id"}, 400)
                return
            try:
                from video_director import editor as vd_editor
                from video_director import matting as vd_matting
                from video_director import assets as vd_assets_mod
                ir = vd_editor.load_composition(pid)
                scene_id, idx_s = target.split("#")
                idx = int(idx_s)
                scene = next(s for s in ir.scenes if s.id == scene_id)
                layer = scene.layers[idx]
                if not layer.asset_id:
                    self._send_json({"error": "该层无素材可抠像"}, 400)
                    return
                provider = vd_matting.get_matting_provider()
                if provider is None:
                    self._send_json({"error": "抠像 Provider 不可用（ffmpeg 未就绪）",
                                     "code": "UNAVAILABLE"}, 503)
                    return
                base = Path(vd_config.composer_storage_root()) / pid
                manifest_path = base / "assets" / "manifest.json"
                existing = []
                if manifest_path.is_file():
                    try:
                        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        existing = []
                src = next((a for a in existing if a.get("id") == layer.asset_id), None)
                if not src or not src.get("file"):
                    self._send_json({"error": f"素材 {layer.asset_id} 不在项目库"}, 404)
                    return
                out_dir = base / "output" / "matting"
                out_dir.mkdir(parents=True, exist_ok=True)
                out_name = f"matte-{layer.asset_id}{Path(src['file']).suffix or '.mp4'}"
                out_path = out_dir / out_name
                vd_matting.ChromaKeyProvider().extract(
                    src["file"], str(out_path),
                    color=payload.get("color", "0x00FF00"),
                    similarity=float(payload.get("similarity", 0.18)),
                    blend=float(payload.get("blend", 0.08)),
                    replace_color=payload.get("replace_color", "black"),
                )
                # 抠像产物注册进项目 manifest（source=extracted，§10 来源链）
                new_assets, _ = vd_assets_mod.build_manifest(
                    [str(out_path)], [], license_note="chromakey-matte")
                new_id = f"{layer.asset_id}-matte"
                have = {a.get("id") for a in existing}
                existing = [a for a in existing if a.get("id") != new_id]
                for k, ref in new_assets.items():
                    ref_dict = json.loads(ref.model_dump_json())
                    ref_dict["id"] = new_id
                    ref_dict["source"] = "extracted"
                    ref_dict["description"] = f"抠像产物（ChromaKey，源自 {layer.asset_id}）"
                    ref_dict["provenance"] = {"provider": "ChromaKey",
                                              "source_asset": layer.asset_id}
                    existing.append(ref_dict)
                manifest_path.parent.mkdir(parents=True, exist_ok=True)
                manifest_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
                self._send_json({"ok": True, "asset_id": new_id, "file": str(out_path)})
            except StopIteration:
                self._send_json({"error": f"目标层 {target} 不存在"}, 404)
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


def _run_confirm_as_workflow(project_id: str) -> str:
    """§四 /api/confirm 同步渲染的 WorkflowRun 登记（渲染中删除保护）。

    start_run 即刻落 RUNNING（_workflow_snapshot 立刻可见）；调用方在
    finalize 成功/失败后 finish_run。登记失败不阻塞渲染本身（run 是
    保护信号，不是渲染前置条件）。
    """
    from video_director import workflow_run as vd_wf
    try:
        return vd_wf.start_run("render_final", project_id=project_id,
                               steps=["render", "verify"])
    except Exception as exc:  # noqa: BLE001
        print(f"[web] confirm run 登记失败（不阻塞渲染）: {exc}", file=sys.stderr)
        return ""


def main(port: int = 8501):
    _load_llm_config()
    try:
        from video_director import workflow_run as _vd_wf
        rec = _vd_wf.reconcile_runs()
        if rec.get("interrupted_runs"):
            print(f"[web] 已收敛 {len(rec['interrupted_runs'])} 个中断任务", file=sys.stderr)
    except Exception as exc:
        print(f"[web] WorkflowRun 恢复检查失败（不阻塞启动）: {exc}", file=sys.stderr)
    try:
        vd_audio.ensure_audio_library()
        # 扩库：风格化 BGM（ffmpeg 合成免费，中文标签），幂等
        vd_audio.generate_style_bgm_library()
    except Exception as exc:
        print(f"[web] 音频库自检失败（不阻塞）: {exc}", file=sys.stderr)
    # §42 Block 开发体验：启动时加载用户自定义块进 Creative Catalog
    try:
        from video_director.graphics import blocks as _vd_gfx_blocks
        n = _vd_gfx_blocks.load_custom_blocks(COMPOSER_ROOT)
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