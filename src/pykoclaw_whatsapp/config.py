"""WhatsApp plugin configuration."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


class WhatsAppSettings(BaseSettings):
    """WhatsApp plugin configuration."""

    auth_dir: Path = Field(default=Path.home() / ".pykoclaw" / "whatsapp" / "auth")
    trigger_name: str = Field(default="Andy")
    session_db: Path = Field(
        default=Path.home() / ".pykoclaw" / "whatsapp" / "session.db"
    )

    class Config:
        env_prefix = "PYKOCLAW_WA_"


_config: WhatsAppSettings | None = None


def get_config() -> WhatsAppSettings:
    """Get WhatsApp plugin configuration."""
    global _config
    if _config is None:
        _config = WhatsAppSettings()
    return _config
