"""AI Video Director（独立项目）。

核心引擎 = Composition IR 流水线：
    用户意图 → 风格 → 素材理解（VisionProvider 六级链）→ 语义匹配 →
    Composition IR → Validator → Renderer → QC → final.mp4

Narration Timeline 是唯一主时间轴；LLM 只产出结构化 JSON，禁止直接生成
FFmpeg/Shell 命令；素材不足诚实 MISSING，绝不编造。
MoneyPrinterTurbo / Short Video Factory / Hypit / HyperFrames / video-use /
VideoLingo 仅作参考实现，本系统与其零代码耦合。
"""

COMPOSER_VERSION = "0.1.0"
TIMELINE_SCHEMA_VERSION = 1


def _patch_edge_tts_endpoint() -> None:
    """edge-tts 端点自愈补丁（2026-10 微软封禁旧合成端点，上游 #490/#491）。

    微软 2026-10 起 403 拒绝 wss://speech.platform.bing.com/.../edge/v1，
    上游修复（切 api.msedgeservices.com）尚未发版，pip 最新版同样失败。
    与上游 PR #491 同款：换端点 + 修 locale 依赖的 Date 解析。
    上游发版后新 WSS_URL 与本补丁 URL 不同 → 补丁自动 no-op，可删。
    """
    try:
        import edge_tts.constants as _vd_etc
        import edge_tts.drm as _vd_etd
        from email.utils import parsedate_to_datetime as _vd_pdate

        legacy = "speech.platform.bing.com"
        new_url = (
            "wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1"
            f"?Ocp-Apim-Subscription-Key={_vd_etc.TRUSTED_CLIENT_TOKEN}"
        )
        if legacy in str(getattr(_vd_etc, "WSS_URL", "")):
            _vd_etc.WSS_URL = new_url
            try:
                import edge_tts.communicate as _vd_etc_comm
                if legacy in str(getattr(_vd_etc_comm, "WSS_URL", "")):
                    _vd_etc_comm.WSS_URL = new_url
            except ImportError:
                pass

        def _parse_rfc2616_date(date):  # noqa: ANN001 — 猴补上游签名
            try:
                parsed = _vd_pdate(date)
            except (TypeError, ValueError, IndexError):
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=_vd_etd.tz.utc)
            return parsed.timestamp()

        # 无条件替换：locale 无关解析严格优于 strptime（%a/%b 随 locale 变），
        # 上游若已内置同款，替换后行为等价。
        _vd_etd.DRM.parse_rfc2616_date = staticmethod(_parse_rfc2616_date)
    except Exception:
        # 补丁失败（edge-tts 未安装/结构变化）不阻塞系统启动；
        # narration 层自会抛出带上下文的 NarrationError。
        pass


_patch_edge_tts_endpoint()
