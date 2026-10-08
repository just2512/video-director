    def __init__(self, api_key: str = None, base_url: str = None, model: str = None):
        import os

        # 多模型配置中心（前端可配）优先，环境变量兜底
        mc_cfg = {"api_key": "", "base_url": "", "model": ""}
        try:
            from video_director import model_config
            mc = model_config.get_role_config("vision")
            mc_cfg = {"api_key": mc.get("api_key", "") or "",
                      "base_url": mc.get("base_url", "") or "",
                      "model": mc.get("model", "") or ""}
        except Exception:
            pass
        self._api_key = (
            api_key or mc_cfg["api_key"]
            or os.environ.get("COMPOSER_VISION_API_KEY", "")
        )
        self._base_url = (
            base_url or mc_cfg["base_url"]
            or os.environ.get("COMPOSER_VISION_BASE_URL", "https://api.openai.com/v1")
        ).rstrip("/")
        self._model = (
            model or mc_cfg["model"]
            or os.environ.get("COMPOSER_VISION_MODEL", "gpt-4o")
        )

    def available(self) -> bool:
        try:
            from video_director import provider_matrix
            cfg = {"api_key": self._api_key, "base_url": self._base_url, "model": self._model}
            return provider_matrix.state("vision", "vision.frame_analyze", cfg).status == provider_matrix.READY
        except Exception:
            return bool(self._api_key)

    def analyze(self, asset: AssetRef, frame_dir: Path) -> dict:
        import base64
        import urllib.request

        try:
            from video_director import provider_matrix
            provider_matrix.require("vision", "vision.frame_analyze", {
                "api_key": self._api_key, "base_url": self._base_url, "model": self._model
            })
        except provider_matrix.ProviderCapabilityError as exc:
            raise VisionError(str(exc)) from exc
        frames = extract_frames(asset.file, frame_dir / asset.id, frames=4)
        content = [{"type": "text", "text": ANALYZE_PROMPT.format(frame_count=len(frames))}]
        for fp in frames:
            b64 = base64.b64encode(Path(fp).read_bytes()).decode()
            content.append({
                "type": "image_url",