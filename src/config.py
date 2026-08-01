from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    telegram_api_id: int = Field(..., description="API ID for Telethon")
    telegram_api_hash: str = Field(..., description="API Hash for Telethon")
    telegram_session_name: str = "telegram_agent"

    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587
    email_sender: str = Field(..., description="Your sending email")
    email_password: str = Field(..., description="Your 16-char app password")
    email_receiver: str = Field(..., description="Where to send the report")

    openrouter_api_key: str = Field(..., description="Your OpenRouter API Key")

    target_channels: str = Field(
        ..., description="Comma-separated list of channels to read"
    )

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()
