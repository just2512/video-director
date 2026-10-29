"""NVIDIA GLM-5.3-Flash request options are scoped and stream-safe."""
from types import SimpleNamespace

import pytest

from video_director.llm_provider import OpenAICompatibleProvider


class FakeCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            return iter([
                # Hidden reasoning is deliberately discarded: only visible content
                # may reach the UI token callback.
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
                    content=None, reasoning_content="private reasoning must never be streamed"
                ))]),
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="O"))]),
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="K"))]),
            ])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))]
        )


def _provider(base_url, model):
    provider = OpenAICompatibleProvider(api_key="not-a-real-key", base_url=base_url, model=model)
    fake = FakeCompletions()
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    return provider, fake


@pytest.mark.parametrize("stream", [False, True])
def test_nvidia_glm_flash_uses_low_reasoning_and_hides_reasoning_by_default(monkeypatch, stream):
    monkeypatch.delenv("VD_LLM_REASONING_EFFORT", raising=False)
    provider, fake = _provider(
        "https://integrate.api.nvidia.com/v1",
        "z-ai/glm-5.3-flash",
    )

    fragments = []
    result = provider.generate_stream("reply OK", on_delta=fragments.append) if stream else provider.generate("reply OK")

    assert result == "OK"
    if stream:
        assert fragments == ["O", "K"]
        assert "private reasoning must never be streamed" not in "".join(fragments)
    kwargs = fake.calls[0]
    assert kwargs["reasoning_effort"] == "low"
    assert kwargs["max_tokens"] == 8192
    assert kwargs["extra_body"] == {"chat_template_kwargs": {"clear_thinking": True}}
    assert kwargs.get("stream", False) is stream


def test_nvidia_glm_flash_reasoning_effort_can_be_overridden(monkeypatch):
    monkeypatch.setenv("VD_LLM_REASONING_EFFORT", "high")
    provider, fake = _provider(
        "integrate.api.nvidia.com/v1",
        "z-ai/glm-5.3-flash",
    )

    assert provider.generate("reply OK") == "OK"
    assert fake.calls[0]["reasoning_effort"] == "high"
    assert fake.calls[0]["max_tokens"] == 8192


def test_other_openai_compatible_models_keep_existing_request_shape(monkeypatch):
    monkeypatch.delenv("VD_LLM_REASONING_EFFORT", raising=False)
    provider, fake = _provider(
        "https://api.deepseek.com/v1",
        "deepseek-chat",
    )

    assert provider.generate("reply OK") == "OK"
    assert "reasoning_effort" not in fake.calls[0]
    assert "extra_body" not in fake.calls[0]

def test_nvidia_glm_stream_error_reports_sanitized_transport_diagnostics(monkeypatch):
    provider, _fake = _provider(
        "https://integrate.api.nvidia.com/v1",
        "z-ai/glm-5.3-flash",
    )

    private_reasoning = "private reasoning must not be present in diagnostics"

    class BrokenCompletions:
        def create(self, **kwargs):
            assert kwargs.get("stream") is True

            def events():
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
                    content=None, reasoning_content=private_reasoning
                ))])
                raise OSError("raw upstream detail must not leak")

            return events()

    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=BrokenCompletions())
    )
    fragments = []
    with pytest.raises(RuntimeError) as caught:
        provider.generate_stream("reply OK", on_delta=fragments.append)

    message = str(caught.value)
    assert fragments == []
    assert "exception_chain=OSError" in message
    assert "reasoning_chunks=1" in message
    assert f"reasoning_chars={len(private_reasoning)}" in message
    assert "visible_chunks=0" in message
    assert private_reasoning not in message
    assert "raw upstream detail must not leak" not in message
