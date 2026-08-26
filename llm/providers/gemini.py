"""Google Gemini LLM provider implementation."""

from typing import Any, Optional, Type
import os
from pydantic import BaseModel
from langchain_core.language_models.chat_models import BaseChatModel

from config.settings import settings
from core.logging import logger
from core.exceptions import LLMException
from llm.base import BaseLLMProvider


class GeminiProvider(BaseLLMProvider):
    """LLM Provider for Google Gemini models via langchain-google-genai."""

    def __init__(
        self,
        model_name: Optional[str] = None,
        api_key: Optional[str] = None,
        temperature: float = 0.1,
        **kwargs
    ):
        super().__init__(
            model_name=model_name or settings.gemini_model,
            temperature=temperature,
            **kwargs
        )
        self.api_key = (
            api_key
            or settings.google_api_key
            or os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
        )
        if not self.api_key:
            logger.warning("GOOGLE_API_KEY / GEMINI_API_KEY is not set. Ensure it is configured before invoking Gemini.")

        self._model: Optional[BaseChatModel] = None

    def get_chat_model(self) -> BaseChatModel:
        """Instantiate and return ChatGoogleGenerativeAI."""
        if self._model is not None:
            return self._model

        try:
            from langchain_google_genai import ChatGoogleGenerativeAI

            self._model = ChatGoogleGenerativeAI(
                model=self.model_name,
                google_api_key=self.api_key,
                temperature=self.temperature,
                convert_system_message_to_human=False,
                **self.extra_kwargs
            )
            return self._model
        except Exception as e:
            logger.error(f"Failed to initialize ChatGoogleGenerativeAI: {e}")
            raise LLMException(f"Gemini initialization error: {e}") from e

    def get_structured_llm(self, schema: Type[BaseModel]) -> Any:
        """Returns Gemini model with strict structured output schema."""
        model = self.get_chat_model()
        return model.with_structured_output(schema)
