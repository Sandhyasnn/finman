import json
import logging

from openai import AsyncOpenAI

from .baseai import BaseAIProvider
from .prompts import holding_prompt

logger = logging.getLogger(__name__)

class OpenRouterProvider(BaseAIProvider):
    """
    Provider implementation for OpenRouter.

    OpenRouter exposes an OpenAI-compatible API, so we use
    AsyncOpenAI as the client.

    OpenRouter is treated as ONE independent FinMan provider.
    OpenRouter may internally route the request to a model/provider,
    but that is transparent to the AI manager.
    """
    def __init__(self, ai_config):
        self.ai_config = ai_config
        self.config = ai_config.providers["openrouter"]

        if not self.config.api_key:
            raise ValueError("OpenRouter API key not set")

        self.client = AsyncOpenAI(api_key=self.config.api_key, base_url=self.config.base_url,)

        self.model = self.config.model
        self.timeout = self.config.timeout_seconds
        self.max_tokens = self.config.max_tokens

    async def explain_holding(self, holding: dict) -> dict:
        """
        Generate a structured explanation for one portfolio holding.

        OpenRouter is deliberately not forced into a provider-specific
        JSON schema because the 'openrouter/free' router can select
        different underlying models. We therefore request JSON through
        the common prompt and parse/normalize the response ourselves.
        """
        prompt = holding_prompt(holding, self.ai_config.analysis,)

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                max_tokens=self.max_tokens,
                temperature=0.3,
            )

            message = response.choices[0].message

            # Normal content returned by OpenRouter
            raw_text = (message.content or "").strip()

            # Some reasoning models may put useful text into reasoning.
            # We do not use reasoning as the actual answer because the
            # common provider contract expects the model's final content.
            if not raw_text:
                raise ValueError("OpenRouter returned an empty response")

            # Remove markdown JSON fences if present.
            if raw_text.startswith("```json"):
                raw_text = (raw_text.split("```json", 1)[1].split("```", 1)[0].strip())
            elif raw_text.startswith("```"):
                raw_text = (raw_text.split("```", 1)[1].split("```", 1)[0].strip())

            try:
                parsed = json.loads(raw_text)

            except json.JSONDecodeError:
                logger.warning("OpenRouter JSON decode failed. " "Returning raw response as summary.")

                return {
                    "summary": raw_text[:1500],
                    "insights": [],
                    "citations": None,
                }

            summary = parsed.get("summary", "No summary generated.",)

            insights_raw = parsed.get("insights", [],)

            insights = []

            for item in insights_raw:
                if isinstance(item, dict):
                    q = (item.get("q") or item.get("question") or "")

                    a = (item.get("a") or item.get("answer") or "")

                    if q and a:
                        insights.append({ "q": q, "a": a,})

            return {
                "summary": summary,
                "insights": insights,
                "citations": None,
            }

        except Exception as exc:
            logger.error("OpenRouter generation failed: %s", exc,)
            raise

    async def _health_call(self) -> None:
        """
        Minimal OpenRouter connectivity/model check.

        The BaseAIProvider health_check() wrapper handles timeout
        measurement and standardized UP/DOWN results.
        """

        await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "user",
                    "content": "OK",
                }
            ],
            max_tokens=1,
            temperature=0,
        )

