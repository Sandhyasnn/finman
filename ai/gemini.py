import asyncio
import json
from google import genai
from google.genai import types

from .baseai import BaseAIProvider
from .prompts import holding_prompt

import logging
logger = logging.getLogger(__name__)

class GeminiProvider(BaseAIProvider):
    """
    Provider implementation for Google's Gemini model family.
    Responsible for generating holding explanations using the Gemini API.
    
    Inherits from BaseAIProvider which defines the common interface:
    - explain_holding()
    - _health_call()
    - health_check()  (implemented in base class)
    """
    def __init__(self, ai_config):
        """
        Initialize the Gemini provider with configuration.  
        Args:
            provider_config: object containing:
                - api_key (required)
                - model (e.g. 'gemini-1.5-flash', 'gemini-1.5-pro')
                - timeout_seconds
                - max_tokens
        Raises:
            ValueError: if the API key is missing
        """
        self.ai_config = ai_config
        self.config = ai_config.providers["gemini"]

        if not self.config.api_key:
            raise ValueError("Gemini API key not set")

        self.client = genai.Client(api_key=self.config.api_key)

        # Keep configuration values for convenient access
        self.model = self.config.model
        self.timeout = self.config.timeout_seconds
        self.max_tokens = self.config.max_tokens

    async def explain_holding(self, holding: dict) -> dict:
        """
        Main method: generate explanation + insights for one portfolio holding.  
        Expected return format (enforced by BaseAIProvider):
        {
            "summary":   str,
            "insights":  list[dict] with keys "q" and "a",
            "citations": list[dict] | None
        } 
        For Gemini we currently do NOT receive structured citations.
        """        
        # Prepare the prompt using shared prompt factory function
        prompt = holding_prompt(holding, self.ai_config.analysis)

        try:
            # Because google-genai is synchronous, we run it in a thread
            loop = asyncio.get_running_loop()

            response = await loop.run_in_executor(
                None,                                            # default executor
                lambda: self.client.models.generate_content(
                    model=self.config.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        max_output_tokens=self.max_tokens,
                        temperature=0.3,
                        top_p=0.8,
                        response_mime_type="application/json",
                    ),
                )
            )

            # Get the generated text and clean it up
            raw_text = (response.text or "").strip()

            # Gemini sometimes wraps JSON in markdown code blocks (or fences), so clean them
            if raw_text.startswith("```json"):
                raw_text = raw_text.split("```json")[1].split("```")[0].strip()
            elif raw_text.startswith("```"):
                raw_text = raw_text.split("```")[1].strip()

            # Try to parse as JSON (this is what we asked for via mime type)
            try:
                parsed = json.loads(raw_text)
            except json.JSONDecodeError as e:
                logger.warning("Gemini JSON decode failed: %s", e)
                    # If it looks like JSON but truncated, don't show raw blob
                if raw_text.strip().startswith("{"):
                    return {
                        "summary": "AI response was truncated due to token limit.",
                        "insights": [],
                        "citations": None,
                    }

                return {
                    "summary": raw_text[:1500],
                    "insights": [],
                    "citations": None,
                }

            # Extract fields with safe defaults
            summary = parsed.get("summary", "No summary generated.")
            insights_raw = parsed.get("insights", [])

            # Normalize insight items — accept both "q"/"a" and "question"/"answer"
            insights = []
            for item in insights_raw:
                if isinstance(item, dict):
                    q = item.get("q") or item.get("question", "")
                    a = item.get("a") or item.get("answer", "")
                    if q and a:                         # only keep valid Q&A pairs
                        insights.append({"q": q, "a": a})

            return {
                "summary": summary,
                "insights": insights,
                "citations": None,              # Gemini doesn't provide citations
            }

        except Exception as e:
            error_text = str(e)
            logger.warning("Gemini generation failed: %s", error_text)
            raise
        
    async def _health_call(self) -> None:
        """
        Minimal smoke test to check if the API is reachable.
        Called by health_check() wrapper in BaseAIProvider.
        Must raise exception when unhealthy.
        """
        loop = asyncio.get_running_loop()

        def health_check():
            chat = self.client.chats.create(model=self.config.model)
            chat.send_message("OK")

        await loop.run_in_executor(None, health_check)
        
    
