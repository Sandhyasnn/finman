import asyncio
import json
import logging

from mistralai.client import Mistral

from .baseai import BaseAIProvider
from .prompts import holding_prompt

logger = logging.getLogger(__name__)

class MistralProvider(BaseAIProvider):
    """
    Provider implementation for Mistral models.

    Uses the common FinMan AI provider interface:
        - explain_holding()
        - _health_call()

    The provider receives the complete ai_config because the common
    holding prompt uses ai_config.analysis.
    """
    def __init__(self, ai_config):
        """
        Initialize the Mistral provider from the central AI configuration.
        """

        self.ai_config = ai_config
        self.config = ai_config.providers["mistral"]

        if not self.config.api_key:
            raise ValueError("Mistral API key not set")

        self.client = Mistral(api_key=self.config.api_key)

        self.model = self.config.model
        self.timeout = self.config.timeout_seconds
        self.max_tokens = self.config.max_tokens

    async def explain_holding(self, holding: dict) -> dict:
        """
        Generate an AI explanation for one portfolio holding.
        Returns the common FinMan provider format:
        {
            "summary": str,
            "insights": [
                {
                    "q": str,
                    "a": str
                }
            ],
            "citations": None
        }
        """
        # --------------------------------------------------------
        # Build the common FinMan prompt.
        # --------------------------------------------------------
        prompt = holding_prompt(holding, self.ai_config.analysis)

        try:
            # Mistral's Python SDK is synchronous, so run it in
            # an executor to avoid blocking the async event loop.
            loop = asyncio.get_running_loop()

            response = await loop.run_in_executor(
                None,
                lambda: self.client.chat.complete(
                    model=self.model,
                    messages=[
                        {
                            "role": "user",
                            "content": prompt,
                        }
                    ],
                    max_tokens=self.max_tokens,
                    temperature=0.3,
                    response_format={
                        "type": "json_object"
                    },
                )
            )

            # ----------------------------------------------------
            # Extract response text.
            # ----------------------------------------------------
            raw_text = (response.choices[0].message.content or "").strip()

            # Mistral JSON mode guarantees valid JSON, but retain
            # explicit parsing so the provider always returns the
            # common FinMan structure.
            parsed = json.loads(raw_text)

            # ----------------------------------------------------
            # Extract summary.
            # ----------------------------------------------------
            summary = parsed.get("summary", "No summary generated.")

            # ----------------------------------------------------
            # Normalize insights.
            #
            # Accept both:
            #     {"q": "...", "a": "..."}
            #
            # and:
            #     {"question": "...", "answer": "..."}
            #
            # to remain consistent with the Gemini provider.
            # ----------------------------------------------------
            insights_raw = parsed.get("insights", [])

            insights = []

            for item in insights_raw:
                if not isinstance(item, dict):
                    continue

                q = (item.get("q") or item.get("question") or "")

                a = (item.get("a") or item.get("answer") or "")

                if q and a:
                    insights.append({"q": q, "a": a,})

            return {
                "summary": summary,
                "insights": insights,
                "citations": None,
            }

        except Exception as exc:
            logger.warning("Mistral generation failed: %s", exc)
            raise

    async def _health_call(self) -> None:
        """
        Minimal Mistral API smoke test.

        The base class wraps this call with the configured timeout
        and converts the result into the standard UP/DOWN format.
        """

        loop = asyncio.get_running_loop()

        def health_check():
            self.client.chat.complete(
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

        await loop.run_in_executor(None, health_check)

