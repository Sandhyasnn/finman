import asyncio, time

class BaseAIProvider:
    """
    Common interface and health-check implementation for AI providers.

    Each provider must implement:
        explain_holding() - generate an AI explanation
        _health_call()    - perform a lightweight provider health check

    The base class provides the common health-check wrapper, including
    timeout enforcement, latency measurement and standardized results.
    """
    async def explain_holding(self, holding: dict) -> dict:
        """
        Core method that every provider must implement.

        Takes a portfolio holding (dictionary with ticker, name, amount, etc.)
        and returns a structured AI-generated explanation.

        Return format (must be strictly followed):
        {
            "summary":   str                     # short paragraph summary
            "insights":  list[dict]              # list of {"q": question, "a": answer}
            "citations": list[dict] | None       # optional list of {"title", "url", ...}
        }

        Raises:
            NotImplementedError: if subclass doesn't override this method
        """        
        raise NotImplementedError("Concrete provider must implement explain_holding()")
          
    async def _health_call(self) -> None:
        """
        Provider-specific minimal health check.

        Purpose: verify that API key is valid and endpoint is reachable.
        Should be as lightweight as possible (small prompt, tiny output).

        Implementation requirements:
        - Must be async (even if it uses sync client under the hood)
        - Should raise an Exception when the service is unreachable / misconfigured
        - No need to parse or validate the response content deeply

        Called indirectly via health_check() wrapper.
        """
        raise NotImplementedError("Concrete provider must implement _health_call()")
    
    async def health_check(self) -> dict:
        """
        Public health check method — used by monitoring / startup probes.

        Wraps the provider-specific _health_call() with:
        - asyncio timeout enforcement
        - latency measurement
        - structured result format

        Returns:
            {
                "status":     "UP" | "DOWN",
                "latency_ms": int | None,
                "error":      str | None
            }
        """
        start = time.perf_counter()

        try:
            # Enforce maximum wait time (self.timeout usually comes from config)
            await asyncio.wait_for(
                self._health_call(), 
                timeout=self.timeout,           # defined in concrete class
            )

            latency = int((time.perf_counter() - start) * 1000)

            return {
                "status": "UP",
                "latency_ms": latency,
                "error": None,
            }

        except Exception as e:
            return {
                "status": "DOWN",
                "latency_ms": None,
                "error": str(e),
            }
