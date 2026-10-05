import asyncio
import logging
import time

from .gemini import GeminiProvider
from .groq import GroqProvider
from .openrouter import OpenRouterProvider
from .cloudflare import CloudflareProvider

logger = logging.getLogger(__name__)

# Provide registry of available AI provider classes. The keys are the provider names
# used in the config file, and the values are the corresponding class objects.
_PROVIDER_CLASSES = {
    "gemini": GeminiProvider,
    "groq": GroqProvider,
    "openrouter": OpenRouterProvider,
    "cloudflare": CloudflareProvider,
}

# ============================================================
# HELPER FUNCTIONS
# ============================================================
def _get_sorted_provider_names(ai_config: dict) -> list[str]:
    # --------------------------------------------------------
    # Select enabled providers.
    # --------------------------------------------------------
    providers = ai_config.providers

    # --------------------------------------------------------
    # Sort by priority.
    #
    # This is ONLY ordering.
    # It is NOT fallback logic.
    # --------------------------------------------------------
    return sorted(
        (
            name
            for name, config in providers.items()
            if config.enabled
        ),
        key=lambda name: providers[name].priority
    )

# ============================================================
# PROVIDER CREATION
# ============================================================
def _create_provider(name, ai_config):
    """
    Create a provider from the central AI configuration.

    Providers receive the complete ai_config because the existing provider implementations 
    use ai_config.analysis to build the common prompt.
    """
    provider_config = ai_config.providers[name]

    # Try to get the provider class by type first. Type = "gemini", "groq", etc.
    # The provider_class is an instance itself of the pprovider selected like GeminiProvider or GroqProvider.
    provider_class = _PROVIDER_CLASSES.get(provider_config.type)

    # If the provider class is not found by type, try to get it by the section  name.
    # for e.g. if type = "google-gemini" then the below tries to mathc with the section name "gemini" in the config file.
    if provider_class is None:
        provider_class = _PROVIDER_CLASSES.get(name)

    if provider_class is None:
        raise ValueError(f"Unsupported AI provider '{name}' " f"(type='{provider_config.type}')")

    # When we return the provider class with the config as a paramter, it will create an instance of the
    # provider class with the config passed to its constructor. The  __init__ method is also run now.
    return provider_class(ai_config)

# ============================================================
# HOLDING ADAPTER
# ============================================================
def _build_ai_holding(holding) -> dict:
    """
    Adapter function to convert a FinMan Holding object into the dictionary structure
    expected by the holding_prompt() function, which is used to generate prompts for AI providers.
    """
    return {
        "symbol": holding.security,

        "value": {
            "units": holding.quantity,
            "current": holding.current_value,
        },

        "cost": {
            "per_unit": holding.avg_buy_price,
            "total": holding.cost,
        },

        "current_return": {
            "absolute": holding.gain_value,
            "percent": holding.gain_pct,
            "xirr": None,
        },
    }

# ============================================================
# SINGLE PROVIDER EXECUTION
# ============================================================
async def _run_provider(name, ai_config, ai_holding):
    """
    Run one provider.

    A provider failure is isolated and returned as a structured failure result. 
    It does not affect any other provider.
    """
    start = time.perf_counter()

    try:
        provider = _create_provider(name, ai_config)  # Create the AI provider and initialize it with the configuration
        result = await provider.explain_holding(ai_holding)
        latency_ms = int((time.perf_counter() - start) * 1000 )
        logger.info("AI provider '%s' completed successfully in %d ms", name, latency_ms)
        return {
            "provider": name,
            "model": ai_config.providers[name].model,            
            "priority": ai_config.providers[name].priority,
            "status": "SUCCESS",
            "latency_ms": latency_ms,
            "result": result,
            "error": None,
        }
    except Exception as exc:
        latency_ms = int((time.perf_counter() - start) * 1000 )
        logger.error("AI provider '%s' failed: %s", name, exc)
        return {
            "provider": name,
            "model": ai_config.providers[name].model,            
            "priority": ai_config.providers[name].priority,
            "status": "FAILED",
            "latency_ms": latency_ms,
            "result": None,
            "error": str(exc),
        }

# ============================================================
# HOLDING EXPLANATION
# ============================================================
async def explain_holding(ai_config, holding: dict) -> dict:
    """
    Generate AI explanations for a portfolio holding.

    The FinMan Holding model is converted to the provider-neutral dictionary structure expected 
    by the AI prompt. Each configured provider runs independently, so a failure in one provider 
    does not prevent the others from returning a result.
    Design:
        - All enabled providers receive the same prompt.
        - All providers run concurrently.
        - There are no backup/fallback relationships.
        - Priority is only used for ordering.
        - A failed provider does not stop other providers.
    """
    if not ai_config.enabled:
        return {}
    ai_holding = _build_ai_holding(holding)

    # --------------------------------------------------------
    # Select enabled providers and sort by priority
    # --------------------------------------------------------
    provider_names = _get_sorted_provider_names(ai_config)

    if not provider_names:
        return {}

    # --------------------------------------------------------
    # Run every enabled provider concurrently.
    # --------------------------------------------------------
    results = await asyncio.gather(*(_run_provider(name, ai_config, ai_holding,)
            for name in provider_names ) )

    # --------------------------------------------------------
    # Return all provider results.
    #
    # Top-N filtering is intentionally NOT performed here yet.
    # That will be handled in the next step.
    # --------------------------------------------------------

    return {item["provider"]: item
        for item in results }

# ============================================================
# AI HEALTH CHECK
# ============================================================
async def check_ai_health(ai_config) -> dict:
    """
    Explicitly check all enabled AI providers.
    This function is separate from normal AI generation.

    Normal explain_holding() does NOT call health_check() first,
    so normal requests do not consume an additional API call.    
    """
    if not ai_config.enabled:
        return {
            "enabled": False,
            "status": "DISABLED",
            "providers": {},
        }

    # --------------------------------------------------------
    # Select enabled providers and sort by priority
    # --------------------------------------------------------
    provider_names = _get_sorted_provider_names(ai_config)

    async def check_provider(name):
        try:
            provider = _create_provider(name, ai_config)
            result = await provider.health_check()
            return name, {**result, "priority": ai_config.providers[name].priority, }
        except Exception as exc:
            logger.exception("AI health check failed for provider '%s': %s", name, exc)
            return name, {
                "status": "DOWN",
                "latency_ms": None,
                "error": str(exc),
                "priority": ai_config.providers[name].priority,
            }
        
    results = await asyncio.gather(*(check_provider(name)
                                     for name in provider_names ) )

    providers = dict(results)

    up_count = sum(1
                   for result in providers.values()
                   if result["status"] == "UP"  )

    if up_count == 0:
        overall_status = "DOWN"
    elif up_count == len(providers):
        overall_status = "OK"
    else:
        overall_status = "DEGRADED"

    return {
        "enabled": True,
        "status": overall_status,
        "providers": providers,
    }



