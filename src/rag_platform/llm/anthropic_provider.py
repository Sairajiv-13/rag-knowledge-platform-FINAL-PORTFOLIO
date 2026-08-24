"""Anthropic implementation of LLMProvider (default per ADR 0001).

Retries/backoff: the anthropic SDK already retries 429/5xx with exponential
backoff (max_retries=2 by default) — we deliberately don't wrap another retry
layer around it, to avoid retry amplification.
"""

from collections.abc import AsyncIterator

from anthropic import AsyncAnthropic

from rag_platform.llm.base import LLMResult, LLMUsage, StreamEnd, StreamEvent, TextDelta


class AnthropicProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        client: AsyncAnthropic | None = None,
        max_retries: int = 3,
    ) -> None:
        # Injectable client so tests can pass a stub without patching.
        self._client = client or AsyncAnthropic(api_key=api_key, max_retries=max_retries)
        self.model_name = model

    async def generate(self, *, system: str, user: str, max_tokens: int) -> LLMResult:
        msg = await self._client.messages.create(
            model=self.model_name,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(block.text for block in msg.content if block.type == "text")
        usage = LLMUsage(input_tokens=msg.usage.input_tokens, output_tokens=msg.usage.output_tokens)
        return LLMResult(text=text, usage=usage, model=msg.model)

    async def stream(
        self, *, system: str, user: str, max_tokens: int
    ) -> AsyncIterator[StreamEvent]:
        async with self._client.messages.stream(
            model=self.model_name,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        ) as stream:
            async for delta in stream.text_stream:
                yield TextDelta(text=delta)
            final = await stream.get_final_message()
            yield StreamEnd(
                usage=LLMUsage(
                    input_tokens=final.usage.input_tokens,
                    output_tokens=final.usage.output_tokens,
                ),
                model=final.model,
            )
