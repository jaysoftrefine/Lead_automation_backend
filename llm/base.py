"""Base abstract class for LLM providers."""

from abc import ABC, abstractmethod
from typing import Any, List, Optional, Type
from pydantic import BaseModel
from langchain_core.language_models.chat_models import BaseChatModel


class BaseLLMProvider(ABC):
    """Abstract interface that all LLM provider integrations must implement."""

    def __init__(self, model_name: Optional[str] = None, temperature: float = 0.0, **kwargs):
        self.model_name = model_name
        self.temperature = temperature
        self.extra_kwargs = kwargs

    @abstractmethod
    def get_chat_model(self) -> BaseChatModel:
        """Returns the underlying LangChain BaseChatModel instance."""
        pass

    @abstractmethod
    def get_structured_llm(self, schema: Type[BaseModel]) -> Any:
        """Returns a LangChain runnable that enforces the specified Pydantic output schema."""
        pass

    def bind_tools(self, tools: List[Any]) -> Any:
        """Binds a list of tools to the model for tool-calling/agentic reasoning."""
        model = self.get_chat_model()
        return model.bind_tools(tools)
