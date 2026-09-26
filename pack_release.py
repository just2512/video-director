"""打包分发器：把项目打成给其他人用的自包含目录（分发口径契约）。

解决的问题（Jerry 2026-09-24 提出）：开发机上的资源（音频库/便携 ffmpeg/
参考实现）不等于用户电脑上就有。打包 = 把「运行系统所必需」的资产收进
release/ 目录，他人电脑零依赖可跑。

包含：
- video_director/ + webui/ + run_webui.py + 启动WebUI.bat + requirements.txt
- storage/composer/audio_library/（音频库，Mixkit 免署名 + 自产合成）
- requirements 说明（首次运行 pip install）

不包含（诚实口径）：
- MoneyPrinterTurbo 便携版（参考实现，非依赖）——但它的 lib/python 自带
  ffmpeg.exe，打包时把 ffmpeg 二进制复制进 release/bin/ 实现零依赖
- 用户上传的素材 / 项目产物（运行时生成）

用法：python pack_release.py [输出目录，默认 ./release]
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY_LIB = Path(r"D:\autocut\MoneyPrinterTurbo-Portable-Windows-1.3.7\lib\python")


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "release"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    # 1. 代码
    for item in ("video_director", "webui", "docs", "tests"):
        src = ROOT / item
        if src.is_dir():
            dst = out / item
            shutil.copytree(src, dst,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for f in ("run_webui.py", "启动WebUI.bat", "README.md"):
        src = ROOT / f
        if src.is_file():
            shutil.copy2(src, out / f)

    # 2. 音频库（系统资源，非用户数据）
    audio_src = ROOT / "storage" / "composer" / "audio_library"
    if audio_src.is_dir():
        shutil.copytree(audio_src, out / "storage" / "composer" / "audio_library")

    # 3. ffmpeg 随包零依赖（分发标准：不能让用户因缺依赖无法运行）
    # 打包源优先级：release/bin（已验证版本）> 便携 Python imageio_ffmpeg > tools/ffmpeg-btbn
    bin_dir = out / "bin"
    bin_dir.mkdir(exist_ok=True)
    copied_ff = []
    src_bins = [ROOT / "release" / "bin",
                Path(r"D:\autocut\tools\ffmpeg-btbn") / "ffmpeg-master-latest-win64-gpl" / "bin"]
    for src in src_bins:
        if not (src.is_dir() and any(src.glob("ffmpeg*.exe"))):
            continue
        for exe in sorted(src.glob("*.exe")):
            if (bin_dir / exe.name).is_file():
                continue
            shutil.copy2(exe, bin_dir / exe.name)
            copied_ff.append(exe.name)
        if copied_ff:
            break
    # 便携 Python 的 imageio_ffmpeg 作为补充源（ffprobe 等）
    if not any((bin_dir / "ffprobe.exe").is_file() for _ in [0]):
        for exe in PY_LIB.glob(r"Lib\site-packages\imageio_ffmpeg\binaries\*.exe"):
            shutil.copy2(exe, bin_dir / exe.name)
            copied_ff.append(exe.name)

    # 4. 使用说明（首次运行依赖）
    (out / "使用说明.txt").write_text(
        "AI Video Director 分发包\n"
        "========================\n"
        "首次运行：\n"
        "  1. 安装 Python 3.10+（python.org 下载，勾选 Add to PATH）\n"
        "  2. 双击 启动WebUI.bat（自动安装依赖并打开浏览器）\n"
        "     或手动：pip install -r requirements.txt && python run_webui.py\n"
        "\n"
        "包含：视频引擎（bin/ 内置 ffmpeg，无需另装）、音频库（免署名可商用）、\n"
        "      全部代码。\n"
        "不包含：AI 大模型密钥（可选增强，配了更聪明，不配走规则兜底全部可用）。\n",
        encoding="utf-8")

    # 5. 首启自装依赖的 bat（复写：pip install + run）
    (out / "启动WebUI.bat").write_text(
        "@echo off\r\n"
        "cd /d \"%~dp0\"\r\n"
        "python -c \"import edge_tts\" 2>nul || pip install -r requirements.txt\r\n"
        "start \"\" /min python run_webui.py\r\n",
        encoding="utf-8")

    # 6. 依赖清单
    reqs = ROOT / "requirements.txt"
    if not reqs.is_file():
        (out / "requirements.txt").write_text(
            "edge-tts\npydantic\nloguru\npillow\nfaster-whisper\nopenai\n",
            encoding="utf-8")
    else:
        shutil.copy2(reqs, out / "requirements.txt")

    # 7. 结果核对
    n_py = len(list((out / "video_director").glob("*.py")))
    n_audio = len(list((out / "storage" / "composer" / "audio_library").rglob("*.mp3")))
    print(f"打包完成 → {out}")
    print(f"  代码模块: {n_py} 个 .py")
    print(f"  音频库: {n_audio} 个 mp3")
    print(f"  ffmpeg: {copied_ff or '（未找到，需检查源）'}")
    if not copied_ff:
        print("  [警告] ffmpeg 未打包，目标机器将需要自装", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())