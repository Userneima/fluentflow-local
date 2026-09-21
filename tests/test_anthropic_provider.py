"""Coverage for generating notes with Claude instead of an OpenAI-compatible provider.

Three of the four providers speak the OpenAI chat-completions format and differ
only by base URL; Anthropic does not. The failures that would be silent are
pinned here:

- the pipeline passes a per-call ``temperature`` everywhere, and current Claude
  models reject a request that carries one — forwarding it would make every
  note call fail with a 400;
- ``max_tokens`` is required by the Messages API, and on current models it caps
  thinking and the answer together, so a truncated note must be visible;
- a refusal returns HTTP 200 with no usable text, which read as "the model had
  nothing to say" if the stop reason is not checked before the content;
- vision used to be hardcoded to Qwen, so a multimodal note provider still
  demanded a DashScope key.

No network: a fake client records what the adapter would have sent.
"""

import os
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import backend.core.ai_client as ai_client
from backend.core.ai_client import (
    AiClient,
    _chat,
    _get_client,
    _vision_chat,
    anthropic_max_tokens,
    can_use_multimodal,
    vision_model,
)
from backend.core.ai_config import ANTHROPIC_PROVIDER, DEFAULT_ANTHROPIC_MODEL
from backend.core.local_entry_guards import local_ai_kwargs

NOTE = "# 笔记\n\n正文。"


class _FakeStream:
    def __init__(self, owner, kwargs):
        self._owner = owner
        self._kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def get_final_message(self):
        self._owner.calls.append(self._kwargs)
        return self._owner.message


class _FakeAnthropic:
    """Records Messages API kwargs and returns a canned message."""

    def __init__(self, *, text: str = NOTE, stop_reason: str = "end_turn"):
        self.calls: list[dict] = []
        blocks = [SimpleNamespace(type="text", text=text)] if text is not None else []
        self.message = SimpleNamespace(content=blocks, stop_reason=stop_reason)
        self.messages = SimpleNamespace(stream=lambda **kwargs: _FakeStream(self, kwargs))


def _claude(**kwargs) -> tuple[AiClient, _FakeAnthropic]:
    raw = _FakeAnthropic(**kwargs)
    return AiClient(provider=ANTHROPIC_PROVIDER, raw=raw), raw


class AnthropicRequestShapeTests(TestCase):
    def test_temperature_is_never_forwarded(self):
        # The single change that would break every call: current Claude models
        # removed the sampling parameters and 400 on any of them.
        client, raw = _claude()

        _chat(client, DEFAULT_ANTHROPIC_MODEL, "系统提示", "转录稿", temperature=0.3)

        sent = raw.calls[0]
        for forbidden in ("temperature", "top_p", "top_k"):
            self.assertNotIn(forbidden, sent)

    def test_the_system_prompt_is_a_top_level_field_not_a_message(self):
        client, raw = _claude()

        _chat(client, DEFAULT_ANTHROPIC_MODEL, "系统提示", "转录稿")

        sent = raw.calls[0]
        self.assertEqual(sent["system"], "系统提示")
        self.assertEqual([m["role"] for m in sent["messages"]], ["user"])
        self.assertEqual(sent["messages"][0]["content"], [{"type": "text", "text": "转录稿"}])

    def test_max_tokens_is_always_sent_because_the_api_requires_it(self):
        client, raw = _claude()

        _chat(client, DEFAULT_ANTHROPIC_MODEL, "系统提示", "转录稿")

        self.assertEqual(raw.calls[0]["max_tokens"], anthropic_max_tokens())

    def test_the_token_ceiling_is_configurable_and_floored(self):
        with patch.dict(os.environ, {"ANTHROPIC_MAX_TOKENS": "128000"}):
            self.assertEqual(anthropic_max_tokens(), 128000)
        # A tiny ceiling truncates every note, so it is floored rather than honoured.
        with patch.dict(os.environ, {"ANTHROPIC_MAX_TOKENS": "10"}):
            self.assertEqual(anthropic_max_tokens(), 1024)
        with patch.dict(os.environ, {"ANTHROPIC_MAX_TOKENS": "not-a-number"}):
            self.assertEqual(anthropic_max_tokens(), 64_000)

    def test_the_note_text_is_returned_stripped(self):
        client, _ = _claude(text=f"\n\n{NOTE}\n\n")
        self.assertEqual(_chat(client, DEFAULT_ANTHROPIC_MODEL, "s", "u"), NOTE)


class AnthropicStopReasonTests(TestCase):
    def test_a_refusal_raises_instead_of_returning_an_empty_note(self):
        # HTTP 200 with no usable content: silently returning "" would store an
        # empty note and report success.
        client, _ = _claude(text=None, stop_reason="refusal")

        with self.assertRaises(ValueError) as caught:
            _chat(client, DEFAULT_ANTHROPIC_MODEL, "系统提示", "转录稿")
        self.assertIn("拒绝", str(caught.exception))

    def test_hitting_the_token_ceiling_is_logged_not_swallowed(self):
        client, _ = _claude(text=NOTE, stop_reason="max_tokens")

        with self.assertLogs(ai_client.logger, level="WARNING") as logs:
            result = _chat(client, DEFAULT_ANTHROPIC_MODEL, "系统提示", "转录稿")

        self.assertEqual(result, NOTE)  # the partial note is still usable
        self.assertIn("max_tokens", "\n".join(logs.output))


class AnthropicVisionTests(TestCase):
    def _png(self) -> str:
        from tempfile import NamedTemporaryFile

        handle = NamedTemporaryFile(suffix=".png", delete=False)
        handle.write(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        handle.close()
        self.addCleanup(lambda: os.unlink(handle.name))
        return handle.name

    def test_images_use_anthropic_source_blocks_not_openai_data_urls(self):
        client, raw = _claude()

        _vision_chat(client, DEFAULT_ANTHROPIC_MODEL, "系统提示", "看图", [self._png()])

        blocks = raw.calls[0]["messages"][0]["content"]
        self.assertEqual(blocks[0]["type"], "text")
        self.assertEqual(blocks[1]["type"], "image")
        self.assertEqual(blocks[1]["source"]["type"], "base64")
        self.assertEqual(blocks[1]["source"]["media_type"], "image/png")
        self.assertNotIn("image_url", {block["type"] for block in blocks})

    def test_claude_counts_as_multimodal_so_frames_no_longer_need_qwen(self):
        self.assertTrue(can_use_multimodal(ANTHROPIC_PROVIDER))
        self.assertTrue(can_use_multimodal("qwen"))
        self.assertFalse(can_use_multimodal("deepseek"))

    def test_a_vision_call_reuses_the_claude_note_model(self):
        # Qwen must switch to a vision model or images are dropped; Claude
        # models see images, so switching would only invent a wrong model name.
        self.assertEqual(vision_model(ANTHROPIC_PROVIDER, "claude-sonnet-5"), "claude-sonnet-5")
        self.assertEqual(vision_model(ANTHROPIC_PROVIDER, None), DEFAULT_ANTHROPIC_MODEL)
        with patch.dict(os.environ, {"QWEN_VISION_MODEL": "qwen-vl-max"}):
            self.assertEqual(vision_model("qwen", None), "qwen-vl-max")


class ProviderResolutionTests(TestCase):
    def test_the_client_carries_its_provider_instead_of_being_type_sniffed(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}), \
             patch.object(ai_client, "_anthropic_raw_client", lambda key: f"raw:{key}"):
            client = _get_client(provider=ANTHROPIC_PROVIDER)
        self.assertEqual(client.provider, ANTHROPIC_PROVIDER)
        self.assertEqual(client.raw, "raw:sk-ant-test")

    def test_a_missing_key_names_the_variable_to_set(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            with patch.object(ai_client, "load_dotenv", lambda *a, **k: None):
                with self.assertRaises(ValueError) as caught:
                    _get_client(provider=ANTHROPIC_PROVIDER)
        self.assertIn("ANTHROPIC_API_KEY", str(caught.exception))

    def test_the_default_model_is_configurable(self):
        with patch.dict(os.environ, {"ANTHROPIC_MODEL": "claude-haiku-4-5"}):
            self.assertEqual(vision_model(ANTHROPIC_PROVIDER, None), "claude-haiku-4-5")

    def test_a_missing_sdk_says_how_to_install_it(self):
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def no_anthropic(name, *args, **kwargs):
            if name == "anthropic":
                raise ImportError("No module named 'anthropic'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", no_anthropic):
            with self.assertRaises(ValueError) as caught:
                ai_client._anthropic_raw_client("sk-ant-test")
        self.assertIn("requirements-local.txt", str(caught.exception))


class CredentialMatchingTests(TestCase):
    """A provider must never be handed another provider's key."""

    def test_selecting_claude_attaches_only_the_anthropic_key(self):
        kwargs = local_ai_kwargs(
            deepseek_api_key="sk-deepseek",
            anthropic_api_key="sk-ant",
            ai_provider="anthropic",
        )
        self.assertEqual(kwargs["provider"], "anthropic")
        self.assertEqual(kwargs["api_key"], "sk-ant")

    def test_selecting_claude_without_its_key_attaches_no_key_at_all(self):
        # Falling back to another provider's key would send a DeepSeek secret
        # to Anthropic; leaving it unset lets ai_client raise its own message.
        with patch("backend.core.local_entry_guards.resolve_secret", lambda value, _name: value or ""):
            kwargs = local_ai_kwargs(deepseek_api_key="sk-deepseek", ai_provider="anthropic")
        self.assertEqual(kwargs["provider"], "anthropic")
        self.assertNotIn("api_key", kwargs)

    def test_an_anthropic_only_install_is_picked_up_with_no_provider_selected(self):
        with patch("backend.core.local_entry_guards.resolve_secret", lambda value, _name: value or ""):
            kwargs = local_ai_kwargs(anthropic_api_key="sk-ant")
        self.assertEqual(kwargs["provider"], "anthropic")
        self.assertEqual(kwargs["api_key"], "sk-ant")
