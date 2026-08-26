"""LLM Provider Registry for dynamically resolving and creating model providers."""

from typing import Dict, Type, Optional
from core.logging import logger
from core.exceptions import LLMException
from config.settings import settings
from llm.base import BaseLLMProvider
from llm.providers.gemini import GeminiProvider
from llm.providers.nvidia import NvidiaProvider


class LLMProviderRegistry:
    """Registry pattern for registering and instantiating LLM providers."""

    _providers: Dict[str, Type[BaseLLMProvider]] = {
        "gemini": GeminiProvider,
        "nvidia": NvidiaProvider,
    }

    @classmethod
    def register_provider(cls, name: str, provider_class: Type[BaseLLMProvider]) -> None:
        """Register a new LLM provider class under a key."""
        cls._providers[name.lower()] = provider_class
        logger.info(f"Registered LLM provider '{name}' -> {provider_class.__name__}")

    @classmethod
    def get_provider(
        cls,
        provider_name: Optional[str] = None,
        model_name: Optional[str] = None,
        **kwargs
    ) -> BaseLLMProvider:
        """Instantiate a provider instance by name (defaulting to settings)."""
        name = (provider_name or settings.default_llm_provider).lower()
        if name not in cls._providers:
            raise LLMException(
                f"Unknown LLM provider '{name}'. Available registered providers: {list(cls._providers.keys())}"
            )
        
        provider_cls = cls._providers[name]
        return provider_cls(model_name=model_name, **kwargs)

    @classmethod
    def list_available_providers(cls) -> list[str]:
        return list(cls._providers.keys())
