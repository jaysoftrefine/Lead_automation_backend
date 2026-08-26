"""NVIDIA LLM provider implementation."""

from typing import Any, Optional, Type
import os
from pydantic import BaseModel
from langchain_core.language_models.chat_models import BaseChatModel

from config.settings import settings
from core.logging import logger
from core.exceptions import LLMException
from llm.base import BaseLLMProvider


class NvidiaProvider(BaseLLMProvider):
    """LLM Provider for NVIDIA NIM models via ChatNVIDIA or OpenAI-compatible endpoint."""

    def __init__(
        self,
        model_name: Optional[str] = None,
        api_key: Optional[str] = None,
        temperature: float = 0.1,
        **kwargs
    ):
        super().__init__(
            model_name=model_name or settings.nvidia_model,
            temperature=temperature,
            **kwargs
        )
        self.api_key = api_key or settings.nvidia_api_key or os.environ.get("NVIDIA_API_KEY")
        if not self.api_key:
            logger.warning("NVIDIA_API_KEY is not set. Ensure it is configured before invoking NVIDIA NIM.")

        self._model: Optional[BaseChatModel] = None

    def get_chat_model(self) -> BaseChatModel:
        """Instantiate and return NVIDIA chat model."""
        if self._model is not None:
            return self._model

        try:
            try:
                from langchain_nvidia_ai_endpoints import ChatNVIDIA
                self._model = ChatNVIDIA(
                    model=self.model_name,
                    nvidia_api_key=self.api_key,
                    temperature=self.temperature,
                    **self.extra_kwargs
                )
                return self._model
            except (ImportError, Exception) as inner_err:
                logger.debug(f"Direct ChatNVIDIA import not used, falling back to OpenAI-compatible endpoint: {inner_err}")
                from langchain_community.chat_models import ChatOpenAI
                self._model = ChatOpenAI(
                    model=self.model_name,
                    openai_api_key=self.api_key,
                    openai_api_base="https://integrate.api.nvidia.com/v1",
                    temperature=self.temperature,
                    **self.extra_kwargs
                )
                return self._model

        except Exception as e:
            logger.error(f"Failed to initialize NVIDIA NIM Chat Model: {e}")
            raise LLMException(f"NVIDIA initialization error: {e}") from e

    def get_structured_llm(self, schema: Type[BaseModel]) -> Any:
        """Returns NVIDIA model with structured output schema."""
        model = self.get_chat_model()
        return model.with_structured_output(schema)
