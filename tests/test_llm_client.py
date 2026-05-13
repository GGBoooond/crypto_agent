"""单元测试: ``strategies.llm_client.LLMClient``。

覆盖:
1. DeepSeek provider 默认关闭思维链（``thinking={"type":"disabled"}``）
2. DeepSeek provider 显式开启思维链
3. OpenAI / openai_compatible provider 不应注入 ``thinking``
4. 用户传入的 ``extra_body`` 与 provider 默认 extra_body 合并，用户优先
5. 旧 DeepSeek 配置 (DEEPSEEK_API_KEY/AI_MODEL) 在未填新字段时仍能工作
6. ``chat_completion`` 真正把合并后的参数传给底层 AsyncOpenAI 客户端
7. 不支持的 provider 抛出可读异常

运行: ``python -m pytest tests/test_llm_client.py -v``
"""
from __future__ import annotations

import os
import sys
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from strategies.llm_client import (  # noqa: E402  (sys.path 必须先生效)
    LLMClient,
    PROVIDER_DEEPSEEK,
    PROVIDER_OPENAI,
    PROVIDER_OPENAI_COMPATIBLE,
)


def _build_async_openai_mock() -> MagicMock:
    """构造一个能模拟 ``AsyncOpenAI().chat.completions.create`` 的 mock。"""
    mock_client = MagicMock()
    mock_client.chat = MagicMock()
    mock_client.chat.completions = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value="ok")
    return mock_client


class LLMClientExtraBodyTest(unittest.TestCase):
    """``build_extra_body`` 的 provider 特定逻辑。"""

    def test_deepseek_default_disables_thinking(self) -> None:
        with patch("strategies.llm_client.AsyncOpenAI"):
            client = LLMClient(
                provider=PROVIDER_DEEPSEEK,
                api_key="sk-test",
                base_url="https://api.deepseek.com",
                model="deepseek-v4-pro",
                thinking_enabled=False,
            )
        self.assertEqual(
            client.build_extra_body(),
            {"thinking": {"type": "disabled"}},
        )

    def test_deepseek_can_enable_thinking(self) -> None:
        with patch("strategies.llm_client.AsyncOpenAI"):
            client = LLMClient(
                provider=PROVIDER_DEEPSEEK,
                api_key="sk-test",
                base_url="https://api.deepseek.com",
                model="deepseek-v4-pro",
                thinking_enabled=True,
            )
        self.assertEqual(
            client.build_extra_body(),
            {"thinking": {"type": "enabled"}},
        )

    def test_openai_provider_has_no_thinking_field(self) -> None:
        with patch("strategies.llm_client.AsyncOpenAI"):
            client = LLMClient(
                provider=PROVIDER_OPENAI,
                api_key="sk-test",
                base_url="https://api.openai.com/v1",
                model="gpt-4o-mini",
            )
        self.assertEqual(client.build_extra_body(), {})

    def test_openai_compatible_provider_has_no_thinking_field(self) -> None:
        with patch("strategies.llm_client.AsyncOpenAI"):
            client = LLMClient(
                provider=PROVIDER_OPENAI_COMPATIBLE,
                api_key="sk-test",
                base_url="https://example.com/v1",
                model="any-model",
            )
        self.assertEqual(client.build_extra_body(), {})

    def test_unsupported_provider_raises(self) -> None:
        with patch("strategies.llm_client.AsyncOpenAI"):
            with self.assertRaises(ValueError):
                LLMClient(
                    provider="anthropic",
                    api_key="sk-test",
                    base_url="https://example.com",
                    model="claude-3",
                )


class LLMClientRequestKwargsTest(unittest.TestCase):
    """``build_request_kwargs`` / ``chat_completion`` 行为。"""

    def test_request_kwargs_inject_provider_extra_body(self) -> None:
        with patch("strategies.llm_client.AsyncOpenAI"):
            client = LLMClient(
                provider=PROVIDER_DEEPSEEK,
                api_key="sk-test",
                base_url="https://api.deepseek.com",
                model="deepseek-v4-pro",
                thinking_enabled=False,
            )
        prepared = client.build_request_kwargs(
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.2,
        )
        self.assertEqual(prepared["model"], "deepseek-v4-pro")
        self.assertEqual(
            prepared["extra_body"],
            {"thinking": {"type": "disabled"}},
        )

    def test_user_extra_body_takes_precedence_over_provider_default(self) -> None:
        with patch("strategies.llm_client.AsyncOpenAI"):
            client = LLMClient(
                provider=PROVIDER_DEEPSEEK,
                api_key="sk-test",
                base_url="https://api.deepseek.com",
                model="deepseek-v4-pro",
                thinking_enabled=False,
            )
        prepared = client.build_request_kwargs(
            messages=[{"role": "user", "content": "hi"}],
            extra_body={
                "thinking": {"type": "enabled"},
                "custom_flag": True,
            },
        )
        self.assertEqual(
            prepared["extra_body"]["thinking"],
            {"type": "enabled"},
        )
        self.assertTrue(prepared["extra_body"]["custom_flag"])

    def test_openai_request_omits_extra_body_when_no_user_payload(self) -> None:
        with patch("strategies.llm_client.AsyncOpenAI"):
            client = LLMClient(
                provider=PROVIDER_OPENAI,
                api_key="sk-test",
                base_url="https://api.openai.com/v1",
                model="gpt-4o-mini",
            )
        prepared = client.build_request_kwargs(
            messages=[{"role": "user", "content": "hi"}],
        )
        self.assertNotIn("extra_body", prepared)

    def test_explicit_model_override_is_respected(self) -> None:
        with patch("strategies.llm_client.AsyncOpenAI"):
            client = LLMClient(
                provider=PROVIDER_OPENAI,
                api_key="sk-test",
                base_url="https://api.openai.com/v1",
                model="gpt-4o-mini",
            )
        prepared = client.build_request_kwargs(
            messages=[{"role": "user", "content": "hi"}],
            model="gpt-4o",
        )
        self.assertEqual(prepared["model"], "gpt-4o")


class LLMClientChatCompletionTest(unittest.TestCase):
    """端到端: ``chat_completion`` 把合并后参数传给底层 AsyncOpenAI。"""

    def test_chat_completion_forwards_merged_kwargs(self) -> None:
        async_client_mock = _build_async_openai_mock()
        with patch(
            "strategies.llm_client.AsyncOpenAI",
            return_value=async_client_mock,
        ):
            client = LLMClient(
                provider=PROVIDER_DEEPSEEK,
                api_key="sk-test",
                base_url="https://api.deepseek.com",
                model="deepseek-v4-pro",
                thinking_enabled=False,
            )
            result = asyncio.run(
                client.chat_completion(
                    messages=[{"role": "user", "content": "hi"}],
                    temperature=0.2,
                    max_tokens=128,
                )
            )

        self.assertEqual(result, "ok")
        async_client_mock.chat.completions.create.assert_awaited_once()
        called_kwargs = async_client_mock.chat.completions.create.call_args.kwargs
        self.assertEqual(called_kwargs["model"], "deepseek-v4-pro")
        self.assertEqual(called_kwargs["temperature"], 0.2)
        self.assertEqual(called_kwargs["max_tokens"], 128)
        self.assertEqual(
            called_kwargs["extra_body"],
            {"thinking": {"type": "disabled"}},
        )


class LLMClientSettingsFallbackTest(unittest.TestCase):
    """旧 DeepSeek 配置 (DEEPSEEK_API_KEY / AI_MODEL) 在未填新字段时仍工作。"""

    _LEGACY_FIELDS = (
        "deepseek_api_key",
        "deepseek_base_url",
        "ai_model",
        "llm_provider",
        "llm_api_key",
        "llm_base_url",
        "llm_model",
        "llm_thinking_enabled",
    )

    def test_falls_back_to_legacy_deepseek_settings(self) -> None:
        from config import settings as global_settings

        snapshot = {name: getattr(global_settings, name) for name in self._LEGACY_FIELDS}
        try:
            global_settings.deepseek_api_key = "legacy-deepseek-key"
            global_settings.deepseek_base_url = "https://legacy.deepseek.com"
            global_settings.ai_model = "deepseek-v4-pro"
            global_settings.llm_provider = "deepseek"
            global_settings.llm_api_key = ""
            global_settings.llm_base_url = ""
            global_settings.llm_model = ""
            global_settings.llm_thinking_enabled = False

            with patch("strategies.llm_client.AsyncOpenAI") as async_openai_cls:
                async_openai_cls.return_value = MagicMock()
                client = LLMClient()

            self.assertEqual(client.api_key, "legacy-deepseek-key")
            self.assertEqual(client.base_url, "https://legacy.deepseek.com")
            self.assertEqual(client.model, "deepseek-v4-pro")
            self.assertEqual(client.provider, PROVIDER_DEEPSEEK)
            self.assertEqual(
                client.build_extra_body(),
                {"thinking": {"type": "disabled"}},
            )
            async_openai_cls.assert_called_once_with(
                api_key="legacy-deepseek-key",
                base_url="https://legacy.deepseek.com",
            )
        finally:
            for name, value in snapshot.items():
                setattr(global_settings, name, value)

    def test_openai_provider_resolves_default_base_url(self) -> None:
        from config import settings as global_settings

        snapshot = {name: getattr(global_settings, name) for name in self._LEGACY_FIELDS}
        try:
            global_settings.llm_provider = "openai"
            global_settings.llm_api_key = "sk-openai"
            global_settings.llm_base_url = ""
            global_settings.llm_model = "gpt-4o-mini"
            global_settings.llm_thinking_enabled = False

            with patch("strategies.llm_client.AsyncOpenAI"):
                client = LLMClient()

            self.assertEqual(client.provider, PROVIDER_OPENAI)
            self.assertEqual(client.api_key, "sk-openai")
            self.assertEqual(client.base_url, "https://api.openai.com/v1")
            self.assertEqual(client.model, "gpt-4o-mini")
            self.assertEqual(client.build_extra_body(), {})
        finally:
            for name, value in snapshot.items():
                setattr(global_settings, name, value)


if __name__ == "__main__":
    unittest.main()
