"""统一的 LLM 客户端封装（OpenAI 协议兼容）。

设计目标:
    - 所有策略只通过 ``LLMClient.chat_completion`` 调用模型，避免在各处散落
      ``AsyncOpenAI`` 实例 / 模型名 / provider 特殊参数。
    - 自动按 provider 注入 provider 专属参数：
        * deepseek: ``extra_body={"thinking": {"type": "enabled|disabled"}}``
          （DeepSeek V4 系列默认开启思维链；非推理任务一般应关闭以避免
            token 全部被思考阶段消耗导致 ``content`` 为空）
    - 用户传入的 ``extra_body`` 优先级高于 provider 默认值，便于按需覆盖。

支持的 provider:
    - ``deepseek``           DeepSeek 官方 / 同协议网关
    - ``openai``             OpenAI 官方
    - ``openai_compatible``  其他 OpenAI 协议兼容服务（Azure / 通义 / vLLM 等）
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from openai import AsyncOpenAI

from config import settings


PROVIDER_DEEPSEEK = "deepseek"
PROVIDER_OPENAI = "openai"
PROVIDER_OPENAI_COMPATIBLE = "openai_compatible"

_SUPPORTED_PROVIDERS = {
    PROVIDER_DEEPSEEK,
    PROVIDER_OPENAI,
    PROVIDER_OPENAI_COMPATIBLE,
}


class LLMClient:
    """OpenAI 协议兼容模型的统一封装。"""

    def __init__(
        self,
        *,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        thinking_enabled: Optional[bool] = None,
    ) -> None:
        self.provider = self._normalize_provider(provider)
        self.api_key = api_key if api_key is not None else settings.get_llm_api_key()
        self.base_url = base_url if base_url is not None else settings.get_llm_base_url()
        self.model = model if model is not None else settings.get_llm_model()
        self.thinking_enabled = (
            settings.llm_thinking_enabled if thinking_enabled is None else thinking_enabled
        )
        self.async_client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

    @staticmethod
    def _normalize_provider(provider: Optional[str]) -> str:
        raw = provider if provider is not None else settings.get_llm_provider()
        normalized = (raw or PROVIDER_DEEPSEEK).strip().lower()
        if normalized not in _SUPPORTED_PROVIDERS:
            raise ValueError(
                f"Unsupported LLM provider: {raw!r}. "
                f"Expected one of {sorted(_SUPPORTED_PROVIDERS)}."
            )
        return normalized

    def build_extra_body(self) -> Dict[str, Any]:
        """生成 provider 默认要注入的 ``extra_body``。

        当前仅 DeepSeek 需要 ``thinking`` 字段；其他 provider 返回空 dict。
        """
        if self.provider == PROVIDER_DEEPSEEK:
            return {
                "thinking": {
                    "type": "enabled" if self.thinking_enabled else "disabled",
                }
            }
        return {}

    @staticmethod
    def _merge_extra_body(
        provider_extra: Dict[str, Any],
        user_extra: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """合并 provider 默认 extra_body 与用户传入 extra_body，用户优先。"""
        if not provider_extra and not user_extra:
            return {}
        if not user_extra:
            return dict(provider_extra)
        merged: Dict[str, Any] = dict(provider_extra)
        merged.update(user_extra)
        return merged

    def build_request_kwargs(self, **kwargs: Any) -> Dict[str, Any]:
        """构造最终发送给 ``chat.completions.create`` 的关键字参数。

        - 自动填充 ``model``（缺省时使用 provider 配置）
        - 自动合并 ``extra_body``（provider 默认 + 用户传入，用户优先）
        """
        prepared: Dict[str, Any] = dict(kwargs)
        prepared.setdefault("model", self.model)
        merged_extra = self._merge_extra_body(
            self.build_extra_body(),
            prepared.pop("extra_body", None),
        )
        if merged_extra:
            prepared["extra_body"] = merged_extra
        return prepared

    async def chat_completion(self, **kwargs: Any) -> Any:
        """统一的 ChatCompletion 入口。"""
        prepared = self.build_request_kwargs(**kwargs)
        return await self.async_client.chat.completions.create(**prepared)
