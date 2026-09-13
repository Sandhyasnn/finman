import asyncio
import json
from openai import OpenAI

from .baseai import BaseAIProvider
from .prompts import holding_prompt

import logging
logger = logging.getLogger(__name__)

class PerplexityProvider(BaseAIProvider):
    """
    Provider implementation for Perplexity AI (using their OpenAI-compatible API).
    Supports citations — unlike the Gemini provider.

    Uses the official OpenAI Python client pointed at Perplexity's base URL.
    """
    def __init__(self, ai_config):
        """
        Args:
            provider_config: object with:
                - api_key           (required)
                - base_url          (default: https://api.perplexity.ai)
                - model             (e.g. sonar-medium-online, llama-3.1-sonar-large-128k-online)
                - timeout_seconds
                - max_tokens

        Creates an OpenAI client instance configured for Perplexity.
        """
        self.ai_config = ai_config
        self.config = ai_config.providers["perplexity"]

        self.client = OpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
        )

        self.model = self.config.model
        self.timeout = self.config.timeout_seconds
        self.max_tokens = self.config.max_tokens

    async def explain_holding(self, holding: dict) -> dict:
        """
        Generate structured explanation + insights + citations (if available).

        Perplexity online models often return citations automatically.
        We use a strong system prompt to enforce JSON output.
        """
        prompt = holding_prompt(holding, self.ai_config.analysis)

        # Encourage JSON + citations
        system_message = (
            "You are a financial portfolio analysis assistant. "
            "Respond in strict JSON format with fields: "
            "summary (string), insights (array of {q, a}), "
            "and citations (array of {title, url}). "
            "Citations must be valid URLs."
        )

        try:
            loop = asyncio.get_running_loop()

            response = await loop.run_in_executor(
                None,
                lambda: self.client.chat.completions.create(
                    model=self.model,
                    temperature=0.3,
                    max_tokens=self.max_tokens,
                    messages=[
                        {"role": "system", "content": system_message},
                        {"role": "user", "content": prompt},
                    ],
                ),
            )

            # Extract content from first (and usually only) choice
            raw_text = response.choices[0].message.content.strip()

            # Perplexity sometimes still wraps in ```json ... ```
            # Clean markdown fences
            if raw_text.startswith("```json"):
                raw_text = raw_text.split("```json")[1].split("```")[0].strip()
            elif raw_text.startswith("```"):
                raw_text = raw_text.split("```")[1].strip()

            # Attempt to parse the expected JSON
            try:
                parsed = json.loads(raw_text)
            except json.JSONDecodeError:
                logger.warning("Perplexity returned non-JSON response")
                return {
                    "summary": raw_text[:1500],
                    "insights": [],
                    "citations": [],
                }

            summary = parsed.get("summary", "No summary generated.")
            insights_raw = parsed.get("insights", [])
            citations_raw = parsed.get("citations", [])

            # Normalize insight format (accepts both "q"/"a" and "question"/"answer")
            insights = []
            for item in insights_raw:
                if isinstance(item, dict):
                    q = item.get("q") or item.get("question", "")
                    a = item.get("a") or item.get("answer", "")
                    if q and a:
                        insights.append({"q": q, "a": a})

            # Normalize citations — ensure we have at least a URL
            citations = []
            for item in citations_raw:
                if isinstance(item, dict):
                    title = item.get("title")
                    url = item.get("url")
                    if url:
                        citations.append({
                            "title": title or url,
                            "url": url,
                            "source": "Perplexity"
                        })

            return {
                "summary": summary,
                "insights": insights,
                "citations": citations,
            }

        except Exception as e:
            logger.error(f"Perplexity generation failed: {e}")
            return {
                "summary": "AI generation failed.",
                "insights": [],
                "citations": [],
            }

    async def _health_call(self) -> None:
        """
        Lightweight connectivity + auth check.
        Uses tiniest possible request.
        """        
        loop = asyncio.get_running_loop()

        await loop.run_in_executor(
            None,
            lambda: self.client.chat.completions.create(
                model=self.model,
                temperature=0,
                max_tokens=1,
                messages=[
                    {"role": "user", "content": "OK"}
                ],
            ),
        )
