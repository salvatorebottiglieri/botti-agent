"""Factory for creating LLM clients based on provider."""

import logging
from typing import Literal

from cortex.llm.base import LLMClient
from cortex.llm.providers.openai import OpenAIClient
from cortex.config.models import Settings

logger = logging.getLogger(__name__)

PROVIDER_MAP: dict[str, type[LLMClient]] = {
    "openai": OpenAIClient,
}


class LLMClientFactory:
    """
    Factory for creating LLM client instances.
    
    Example:
        settings = get_settings()
        factory = LLMClientFactory(settings)
        client = factory.create()  # Creates OpenAI client by default
        
        # Or specify provider
        client = factory.create(provider="anthropic")
    """

    def __init__(self, settings: Settings):
        self._settings = settings

    def create(self, provider: str | None = None) -> LLMClient:
        """
        Create an LLM client for the specified provider.
        
        Args:
            provider: Provider name (defaults to settings.llm_provider)
            
        Returns:
            Configured LLM client instance
            
        Raises:
            ValueError: If provider is not supported
        """
        provider = provider or self._settings.llm_provider
        
        client_class = PROVIDER_MAP.get(provider)
        if client_class is None:
            raise ValueError(
                f"Unsupported LLM provider: {provider}. "
                f"Supported: {list(PROVIDER_MAP.keys())}"
            )
        
        logger.info(f"Creating LLM client for provider: {provider}")
        return client_class.from_settings(self._settings)

    @classmethod
    def register_provider(cls, name: str, client_class: type[LLMClient]) -> None:
        """
        Register a new LLM provider.
        
        Args:
            name: Provider name (e.g., 'anthropic')
            client_class: LLMClient subclass
        """
        PROVIDER_MAP[name] = client_class
        logger.debug(f"Registered LLM provider: {name}")
