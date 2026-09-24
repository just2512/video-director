"""抠像 Provider（方案 §87 Segmentation/Matting 层，ffmpeg 零依赖实现）。

ChromaKeyProvider：绿幕/蓝幕抠像——ffmpeg colorkey 滤镜，无任何模型依赖。
适用：绿幕录制素材（talking-head 前置）。非绿幕素材会诚实失败（不瞎抠）。

定位（方案 §87 约束）：只承担「主体脱离背景」的抠像执行，不做视频语义理解。
真值边界：rembg/Modnet 等人像模型 provider 留扩展位，未装不谎报。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from loguru import logger

from video_director import config as config


class MattingError(Exception):
    pass


class ChromaKeyProvider:
    """ffmpeg colorkey 绿幕抠像。available() 恒 True（ffmpeg 即可用）。"""

    name = "ChromaKey"

    def available(self) -> bool:
        from video_director import prober
        return prober._resolve_ffmpeg() is not None

    def extract(
        self,
        video_path: str,
        out_path: str,
        color: str = "0x00FF00",
        similarity: float = 0.18,
        blend: float = 0.08,
        replace_color: str = "black",
    ) -> str:
        """抠像输出（绿→替换底色）。

        color: 被抠除的颜色（默认纯绿；蓝幕改 0x0000FF）
        similarity: 容差（0-1，越大抠越狠）
        blend: 边缘羽化
        replace_color: 抠掉区域的替换底色（ffmpeg color 名或 0xRRGGBB）
        """
        import subprocess
        from video_director import prober
        ff = prober._resolve_ffmpeg()
        if ff is None:
            raise MattingError("ffmpeg 不可用")
        if not Path(video_path).is_file():
            raise MattingError(f"素材不存在: {video_path}")
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        # probe 分辨率给底色层用（color 滤镜不吃 iw/sh 表达式）
        probe = prober.probe_media(video_path)
        w = probe.width or 1920
        h = probe.height or 1080
        vf = (
            f"[0:v]colorkey={color}:{similarity}:{blend}[fg];"
            f"color=c={replace_color}:s={w}x{h}[bg];"
            f"[bg][fg]overlay=shortest=1,format=yuv420p"
        )
        cmd = [
            ff, "-y", "-loglevel", "error",
            "-i", video_path,
            "-filter_complex", vf,
            "-c:v", config.DEFAULT_VIDEO_CODEC,
            str(out),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300)
        if r.returncode != 0:
            raise MattingError(f"抠像渲染失败: {r.stderr[-300:]}")
        logger.info(f"chromakey: {Path(video_path).name} → {out.name} (color={color})")
        return str(out)


def get_matting_provider() -> Optional[ChromaKeyProvider]:
    """获取抠像 provider（ffmpeg 就绪即返回 ChromaKeyProvider）。"""
    p = ChromaKeyProvider()
    return p if p.available() else None
