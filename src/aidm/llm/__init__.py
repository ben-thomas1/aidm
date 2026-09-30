"""LLM model factory: builds the pydantic-ai chat model from global config.

Targets either an OpenAI-compatible endpoint (e.g. a local llama.cpp server) or,
when ``config.llm.provider`` is set, a first-class provider such as Claude on
Google Cloud Vertex AI. Endpoint, model id, key, and request timeout all come
from ``config.llm``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contextlib import AsyncExitStack

    from pydantic_ai.models import Model


def build_model(stack: AsyncExitStack) -> Model:
    """Build the chat model from the configured provider or OpenAI endpoint."""
    from pydantic_ai.settings import ModelSettings  # noqa: PLC0415

    from aidm.config import get_config  # noqa: PLC0415

    cfg = get_config().llm
    settings = ModelSettings(max_tokens=cfg.max_tokens)

    if cfg.provider is not None:
        from anthropic import AsyncAnthropicVertex  # noqa: PLC0415
        from pydantic_ai.models.anthropic import AnthropicModel  # noqa: PLC0415
        from pydantic_ai.providers.anthropic import AnthropicProvider  # noqa: PLC0415

        vertex_client = AsyncAnthropicVertex(
            region=cfg.provider.region,
            project_id=cfg.provider.project_id,
            timeout=cfg.timeout_s,
        )
        stack.push_async_callback(vertex_client.close)
        return AnthropicModel(
            cfg.model,
            provider=AnthropicProvider(anthropic_client=vertex_client),
            settings=settings,
        )

    from openai import AsyncOpenAI  # noqa: PLC0415 (lazy: keep heavy deps off startup)
    from pydantic_ai.models.openai import OpenAIChatModel  # noqa: PLC0415
    from pydantic_ai.providers.openai import OpenAIProvider  # noqa: PLC0415

    client = AsyncOpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=cfg.timeout_s)
    stack.push_async_callback(client.close)
    return OpenAIChatModel(
        cfg.model,
        provider=OpenAIProvider(openai_client=client),
        settings=settings,
    )
