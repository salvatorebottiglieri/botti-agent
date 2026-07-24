"""Factory for creating LLM clients based on provider."""

import logging

from cortex.config.models import Settings
from cortex.llm.base import LLMClient
from cortex.llm.providers.openai import OpenAIClient

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
                f"Unsupported LLM provider: {provider}. Supported: {list(PROVIDER_MAP.keys())}"
            )

        logger.info("Creating LLM client for provider: %s", provider)
        return client_class.from_settings(self._settings)

    def create_for_module(
        self,
        module: str,
        provider: str | None = None,
    ) -> LLMClient:
        """Create an LLM client for a specific module, wrapped with CircuitBreaker.

        Each module gets its own ``CircuitBreaker`` with thresholds from
        ``Settings``, so failures in one module do not affect another.

        Args:
            module: Module name (e.g. ``"execution"``, ``"memory"``).
            provider: Provider name (defaults to ``settings.llm_provider``).

        Returns:
            A ``CircuitBreakerLLMClient`` wrapping the provider's client.
        """
        from cortex.llm.circuit_breaker import CircuitBreaker
        from cortex.llm.wrapper import CircuitBreakerLLMClient

        client = self.create(provider=provider)
        breaker = CircuitBreaker(
            failure_threshold=self._settings.circuit_breaker_threshold,
            recovery_timeout=self._settings.circuit_breaker_timeout,
            half_open_successes=self._settings.circuit_breaker_half_open_successes,
        )
        return CircuitBreakerLLMClient(client, breaker, module)

    @classmethod
    def register_provider(cls, name: str, client_class: type[LLMClient]) -> None:
        """
        Register a new LLM provider.

        Args:
            name: Provider name (e.g., 'anthropic')
            client_class: LLMClient subclass
        """
        PROVIDER_MAP[name] = client_class
        logger.debug("Registered LLM provider: %s", name)

