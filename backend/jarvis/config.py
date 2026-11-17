import os
import sys
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from jarvis.model_routing import validate_model_routes


def default_data_dir() -> Path:
    if sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return Path(root) / "JarvisLocal"
    root = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(root) / "jarvis-local"


def valid_personal_style_label(value: str) -> str:
    if value != value.strip() or not value.isprintable():
        raise ValueError("Profilname muss druckbar sein und darf keine Rand-Leerzeichen haben")
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Field(default_factory=default_data_dir, alias="JARVIS_DATA_DIR")
    timezone: str = Field(default="Europe/Berlin", min_length=1, max_length=100,
                          alias="JARVIS_TIMEZONE")
    provider: Literal["ollama", "openrouter"] = Field(default="ollama", alias="JARVIS_PROVIDER")
    ollama_url: str = Field(default="http://127.0.0.1:11434", alias="JARVIS_OLLAMA_URL")
    ollama_model: str = Field(default="qwen3:4b-instruct", alias="JARVIS_OLLAMA_MODEL")
    voicestudio_url: str = Field(default="http://127.0.0.1:3900",
                                 alias="JARVIS_VOICESTUDIO_URL")
    voicestudio_api_key: str | None = Field(default=None, repr=False,
                                           alias="JARVIS_VOICESTUDIO_API_KEY")
    voicestudio_model: str = Field(default="tts-1", min_length=1, max_length=100,
                                   alias="JARVIS_VOICESTUDIO_MODEL")
    voicestudio_voice: str = Field(default="default", min_length=1, max_length=100,
                                   alias="JARVIS_VOICESTUDIO_VOICE")
    openrouter_api_key: str | None = Field(default=None, repr=False,
                                           alias="JARVIS_OPENROUTER_API_KEY")
    openrouter_model: str | None = Field(default=None, alias="JARVIS_OPENROUTER_MODEL")
    model_routes: dict[str, dict[str, str]] = Field(default_factory=dict,
                                                   alias="JARVIS_MODEL_ROUTES")
    daily_budget_usd: float = Field(default=1.0, ge=0, allow_inf_nan=False,
                                    alias="JARVIS_DAILY_BUDGET_USD")
    max_output_tokens: int = Field(default=1024, ge=1, le=8192, alias="JARVIS_MAX_OUTPUT_TOKENS")
    ollama_context_tokens: int = Field(default=8192, ge=4096, le=32768,
                                       alias="JARVIS_OLLAMA_CONTEXT_TOKENS")
    max_context_chars: int = Field(default=24_000, ge=4000, le=200_000,
                                   alias="JARVIS_MAX_CONTEXT_CHARS")
    max_steps: int = Field(default=8, ge=1, le=50, alias="JARVIS_MAX_STEPS")
    personal_style_label: str = Field(default="Mein Stil", min_length=1, max_length=60,
                                      alias="JARVIS_PERSONAL_STYLE_LABEL")

    @field_validator("data_dir")
    @classmethod
    def absolute_data_dir(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("Jarvis-Datenordner muss ein absoluter Pfad sein")
        return value

    @field_validator("model_routes")
    @classmethod
    def valid_model_routes(cls, value: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        return validate_model_routes(value)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("Unbekannte IANA-Zeitzone") from exc
        return value

    @field_validator("voicestudio_url")
    @classmethod
    def local_voicestudio_url(cls, value: str) -> str:
        try:
            url = urlsplit(value)
            if (url.scheme != "http" or url.hostname not in
                    {"127.0.0.1", "localhost", "::1"} or url.port is None
                    or url.username or url.password or url.path not in {"", "/"}
                    or url.query or url.fragment):
                raise ValueError("VoiceStudio muss über eine lokale HTTP-Adresse laufen")
        except ValueError as exc:
            raise ValueError("VoiceStudio muss über eine lokale HTTP-Adresse laufen") from exc
        return value.rstrip("/")

    @field_validator("personal_style_label")
    @classmethod
    def clean_personal_style_label(cls, value: str) -> str:
        return valid_personal_style_label(value)


def load_settings() -> Settings:
    return Settings()
