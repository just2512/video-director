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
from video_director import config as vd_config  # noqa: E402
from video_director import pipeline  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"
STORAGE = ROOT / "storage"

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


def _scan_bgm() -> list:
    try:
        lib = vd_audio.scan_library()
        return [a.id for a in lib.values() if a.type == "bgm"]
    except Exception:
        return []


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

        # 确认渲染端点（Gate B 通过 → 正式渲染 final）
        if parsed.path == "/api/confirm":
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            pid = payload.get("project_id", "")
            if not pid:
                self._send_json({"error": "缺少 project_id"}, 400)
                return
            from video_director import confirm as confirm_mod
            try:
                final = confirm_mod.finalize_after_review(pid)
                self._send_json({
                    "ok": True,
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
        if not videos and not images:
            self._send_json({"error": "请至少上传一个视频或图片素材（或使用纯文案模式需先在设置开启）"}, 400)
            return

        w_str, h_str = fields.get("aspect", "1080x1920").split("x")
        width, height = int(w_str), int(h_str)
        voice = fields.get("voice", "zh-CN-XiaoxiaoNeural")
        style_intent = fields.get("style_intent", "") or None
        bgm = fields.get("bgm", "") or None
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
            if video_paths or image_paths:
                result = pipeline.compose_auto(
                    script=script,
                    video_files=video_paths,
                    image_files=image_paths,
                    project_id=project_id,
                    voice=voice,
                    style_intent=style_intent,
                    bgm_asset_id=bgm,
                    width=width,
                    height=height,
                    auto_confirm=(confirm == "direct"),
                )
            else:
                self._send_json({"error": "无素材"}, 400)
                return

            # 返回 JSON（video 走 /file?path= 播放）
            self._send_json({
                "ok": True,
                "elapsed_s": round(time.time() - t0, 1),
                "project_id": project_id,
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
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"AI Video Director → http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8501)