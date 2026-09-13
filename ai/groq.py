import json
from openai import AsyncOpenAI

from .baseai import BaseAIProvider
from .prompts import holding_prompt

import logging
logger = logging.getLogger(__name__)

class GroqProvider(BaseAIProvider):
    def __init__(self, ai_config):
        self.ai_config = ai_config
        self.config = ai_config.providers["groq"]        

        if not self.config.api_key:
            raise ValueError("Groq API key not set")

        # Groq's OpenAI-compatible API endpoint
        self.client = AsyncOpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
        )

        self.model = self.config.model
        self.timeout = self.config.timeout_seconds
        self.max_tokens = self.config.max_tokens

    async def explain_holding(self, holding: dict) -> dict:
        prompt = holding_prompt(holding, self.ai_config.analysis)

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
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
                                        "required": ["q", "a"],
                                        "additionalProperties": False
                                    }
                                }
                            },
                            "required": ["summary", "insights"],
                            "additionalProperties": False
                        }
                    }
                },
            )

            raw_text = (response.choices[0].message.content or "").strip()

            if raw_text.startswith("```json"):
                raw_text = raw_text.split("```json")[1].split("```")[0].strip()
            elif raw_text.startswith("```"):
                raw_text = raw_text.split("```")[1].strip()

            try:
                parsed = json.loads(raw_text)
            except json.JSONDecodeError:
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
                    q = item.get("q") or item.get("question", "")
                    a = item.get("a") or item.get("answer", "")
                    if q and a:
                        insights.append({"q": q, "a": a})

            return {
                "summary": summary,
                "insights": insights,
                "citations": None,
            }

        except Exception as e:
            logger.error("Groq generation failed: %s", e)
            raise

    async def _health_call(self) -> None:
        await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": "OK"}],
            max_tokens=1,
            temperature=0,
        )
