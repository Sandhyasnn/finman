import asyncio
import json
import logging

from cerebras.cloud.sdk import Cerebras

from .baseai import BaseAIProvider
from .prompts import holding_prompt

logger = logging.getLogger(__name__)

class CerebrasProvider(BaseAIProvider):
    """
    Provider implementation for Cerebras Inference.
    Implements the common BaseAIProvider interface:
        - explain_holding()
        - _health_call()
        - health_check() is inherited from BaseAIProvider
    """
    def __init__(self, ai_config):
        """
        Initialize the Cerebras provider from the central AI configuration.
        """
        self.ai_config = ai_config
        self.config = ai_config.providers["cerebras"]

        if not self.config.api_key:
            raise ValueError("Cerebras API key not set")

        self.client = Cerebras(api_key=self.config.api_key)

        self.model = self.config.model
        self.timeout = self.config.timeout_seconds
        self.max_tokens = self.config.max_tokens

    async def explain_holding(self, holding: dict) -> dict:
        """
        Generate an explanation for one portfolio holding.

        Uses the same common holding prompt as the other providers and
        returns the normalized provider-neutral result structure.
        """
        prompt = holding_prompt(holding, self.ai_config.analysis)

        try:
            loop = asyncio.get_running_loop()

            response = await loop.run_in_executor(
                None,
                lambda: self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ],
                    max_tokens=self.max_tokens,
                    temperature=0.3,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "holding_analysis",
                            "strict": True,
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "summary": {
                                        "type": "string"
                                    },
                                    "insights": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "q": {
                                                    "type": "string"
                                                },
                                                "a": {
                                                    "type": "string"
                                                }
                                            },
                                            "required": [
                                                "q",
                                                "a"
                                            ],
                                            "additionalProperties": False
                                        }
                                    }
                                },
                                "required": [
                                    "summary",
                                    "insights"
                                ],
                                "additionalProperties": False
                            }
                        }
                    }
                )
            )

            raw_text = (response.choices[0].message.content or "").strip()

            # Defensive cleanup in case the model returns markdown fences.
            if raw_text.startswith("```json"):
                raw_text = (raw_text.split("```json", 1)[1].split("```", 1)[0].strip())
            elif raw_text.startswith("```"):
                raw_text = (raw_text.split("```", 1)[1].split("```", 1)[0].strip())

            try:
                parsed = json.loads(raw_text)

            except json.JSONDecodeError as exc:
                logger.warning("Cerebras JSON decode failed: %s", exc)

                return {
                    "summary": raw_text[:1500],
                    "insights": [],
                    "citations": None,
                }

            summary = parsed.get("summary", "No summary generated.")

            insights_raw = parsed.get("insights", [])

            insights = []

            for item in insights_raw:
                if isinstance(item, dict):
                    q = (item.get("q") or item.get("question", ""))

                    a = (item.get("a") or item.get("answer", ""))

                    if q and a:
                        insights.append({"q": q, "a": a })

            return {
                "summary": summary,
                "insights": insights,
                "citations": None,
            }

        except Exception as exc:
            logger.warning("Cerebras generation failed: %s", exc)
            raise

    async def _health_call(self) -> None:
        """
        Minimal Cerebras API smoke test.

        The BaseAIProvider.health_check() wrapper supplies the timeout
        and standardized health result.
        """

        loop = asyncio.get_running_loop()

        def health_check():
            self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": "OK"
                    }
                ],
                max_tokens=1,
                temperature=0,
            )

        await loop.run_in_executor(None, health_check)
