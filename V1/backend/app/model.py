from langchain_groq import ChatGroq

from app.core.config import settings


def create_chat_model(temperature: float) -> ChatGroq:
    return ChatGroq(
        model=settings.groq_model,
        api_key=settings.groq_api_key,
        temperature=temperature,
        timeout=20,
        max_retries=1,
    )
