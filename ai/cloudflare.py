import json
import logging

from openai import AsyncOpenAI

from .baseai import BaseAIProvider
from .prompts import holding_prompt

logger = logging.getLogger(__name__)

class CloudflareProvider(BaseAIProvider):
    """
    Provider implementation for Cloudflare Workers AI.

    Workers AI exposes an OpenAI-compatible chat completions API,
    so we use AsyncOpenAI with a Cloudflare-specific base URL.
    """
    def __init__(self, ai_config):
        """
        Initialize the Cloudflare Workers AI provider.

        Configuration is read from:
            ai_config.providers["cloudflare"]
        """
        self.ai_config = ai_config
        self.config = ai_config.providers["cloudflare"]

        if not self.config.api_key:
            raise ValueError("Cloudflare API token not set")

        if not self.config.base_url:
            raise ValueError("Cloudflare base_url not set")

        self.client = AsyncOpenAI(api_key=self.config.api_key, base_url=self.config.base_url)

        self.model = self.config.model
        self.timeout = self.config.timeout_seconds
        self.max_tokens = self.config.max_tokens

    async def explain_holding(self, holding: dict) -> dict:
        """
        Generate an AI explanation for one portfolio holding.
        """
        prompt = holding_prompt(holding, self.ai_config.analysis)

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

            raw_text = (response.choices[0].message.content or "").strip()

            # ----------------------------------------------------
            # Clean markdown JSON fences if the model adds them.
            # ----------------------------------------------------
            if raw_text.startswith("```json"):
                raw_text = (
                    raw_text
                    .split("```json", 1)[1]
                    .split("```", 1)[0]
                    .strip()
                )

            elif raw_text.startswith("```"):
                raw_text = (
                    raw_text
                    .split("```", 1)[1]
                    .split("```", 1)[0]
                    .strip()
                )

            # ----------------------------------------------------
            # Parse JSON.
            # ----------------------------------------------------
            try:
                parsed = json.loads(raw_text)

            except json.JSONDecodeError as exc:
                logger.warning(
                    "Cloudflare JSON decode failed: %s",
                    exc,
                )

                # Keep the provider successful even if the model
                # didn't perfectly follow the JSON instruction.
                return {
                    "summary": raw_text[:1500],
                    "insights": [],
                    "citations": None,
                }

            # ----------------------------------------------------
            # Normalize insights.
            # ----------------------------------------------------
            summary = parsed.get(
                "summary",
                "No summary generated.",
            )

            insights_raw = parsed.get("insights", [])

            insights = []

            for item in insights_raw:
                if isinstance(item, dict):
                    q = (
                        item.get("q")
                        or item.get("question", "")
                    )

                    a = (
                        item.get("a")
                        or item.get("answer", "")
                    )

                    if q and a:
                        insights.append({
                            "q": q,
                            "a": a,
                        })

            return {
                "summary": summary,
                "insights": insights,
                "citations": None,
            }

        except Exception as exc:
            logger.warning(
                "Cloudflare generation failed: %s",
                exc,
            )
            raise

    async def _health_call(self) -> None:
        """
        Minimal Cloudflare Workers AI health check.
        """

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "user",
                    "content": "OK",
                }
            ],
            max_tokens=1,
        )

        if not response.choices:
            raise RuntimeError(
                "Cloudflare returned no choices"
            )