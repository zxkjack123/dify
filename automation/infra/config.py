import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    DIFY_CONSOLE_URL: str
    DIFY_EMAIL: str
    DIFY_PASSWORD: str

    model_config = SettingsConfigDict(
        env_file=os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
        ),
        env_file_encoding="utf-8",
        extra="ignore",
    )
