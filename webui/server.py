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

STATIC_DIR = Path(__file__).resolve().parent / "static"
STORAGE = ROOT / "storage"
LLM_CONFIG_FILE = STORAGE / "composer" / "llm_config.json"

# 配音（中文 + 英文，EdgeTTS 免费零 key）
VOICES = [
    ("zh-CN-XiaoxiaoNeural", "晓晓（女·温柔）"),
    ("zh-CN-XiaoyiNeural", "晓伊（女·活泼）"),
    ("zh-CN-XiaohanNeural", "晓涵（女·自然）"),
    ("zh-CN-XiaomengNeural", "晓梦（女·甜美）"),
    ("zh-CN-XiaoruiNeural", "晓睿（女·成熟）"),
    ("zh-CN-YunxiNeural", "云希（男·阳光）"),
    ("zh-CN-YunjianNeural", "云健（男·沉稳）"),
    ("zh-CN-YunyangNeural", "云扬（男·新闻腔）"),
    ("zh-CN-YunfengNeural", "云枫（男·浑厚）"),
    ("zh-CN-YunhaoNeural", "云皓（男·磁性）"),
    ("zh-CN-liaoning-XiaobeiNeural", "晓北（东北话）"),
    ("zh-CN-shaanxi-XiaoniNeural", "晓妮（陕西话）"),
    ("zh-TW-HsiaoChenNeural", "曉臻（台湾·女）"),
    ("en-US-AriaNeural", "Aria (US Female)"),
    ("en-US-JennyNeural", "Jenny (US Female)"),
    ("en-US-AnaNeural", "Ana (US Child)"),
    ("en-US-GuyNeural", "Guy (US Male)"),
    ("en-US-ChristopherNeural", "Christopher (US Male)"),
    ("en-US-EricNeural", "Eric (US Male)"),
    ("en-GB-RyanNeural", "Ryan (UK Male)"),
    ("en-GB-SoniaNeural", "Sonia (UK Female)"),
    ("en-US-MichelleNeural", "Michelle (US Female)"),
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
}


def _audio_label(asset_id: str, tags: list = None) -> str:
    """音频中文名：映射表 > tags > id。格式「中文（原名）」。"""
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


def _save_llm_config(api_key: str, base_url: str, model: str) -> None:
    """持久化 LLM 配置到 storage/composer/llm_config.json。"""
    try:
        LLM_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        LLM_CONFIG_FILE.write_text(
            json.dumps(
                {"api_key": api_key, "base_url": base_url, "model": model},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"[web] 保存 LLM 配置失败: {exc}", file=sys.stderr)


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
    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path

        if route == "/" or route == "/index.html":
            self._send_html(STATIC_DIR / "index.html")
        elif route == "/api/health":
            self._send_json({"ok": True, "app": "AI Video Director"})
        elif route == "/api/voices":
            self._send_json([{"id": v, "label": l} for v, l in VOICES])
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
                if not cache_file.is_file():
                    import asyncio
                    import edge_tts
                    async def _syn():
                        com = edge_tts.Communicate("这是一段配音试听，检查音色是否合适。", voice)
                        await com.save(str(cache_file))
                    asyncio.run(_syn())
                data = cache_file.read_bytes()
                self._send_bytes(data, "audio/mpeg")
            except Exception as exc:
                self._send_json({"error": f"试听合成失败: {exc}"}, 500)
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

        # LLM 配置（旧端点，保留兼容；内部转存到 model_config 的 llm 角色）
        if parsed.path == "/api/llm_config":
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON（需 UTF-8）"}, 400)
                return
            api_key = (payload.get("api_key") or "").strip()
            base_url = (payload.get("base_url") or "").strip()
            model = (payload.get("model") or "").strip()
            try:
                vd_llm.set_llm_config(api_key, base_url or None, model or None)
                if api_key:
                    _save_llm_config(api_key, base_url, model)
                else:
                    # 清除配置 = 删除持久化文件，回退环境变量
                    try:
                        LLM_CONFIG_FILE.unlink(missing_ok=True)
                    except Exception:
                        pass
                self._send_json({"ok": True, "config": vd_llm.get_llm_config()})
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
                    style_intent=style_intent,
                    bgm_asset_id=bgm,
                    sfx_plan=sfx_plan,
                    width=width,
                    height=height,
                    auto_confirm=(confirm == "direct"),
                    subtitle_mode=fields.get("subtitle_mode", "plain"),
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
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"AI Video Director → http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8501)