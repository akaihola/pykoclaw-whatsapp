"""WhatsApp plugin for pykoclaw."""

from __future__ import annotations

import click
from pydantic_settings import BaseSettings

from pykoclaw.plugins import PykoClawPluginBase

from .config import WhatsAppSettings


class WhatsAppPlugin(PykoClawPluginBase):
    """WhatsApp plugin for pykoclaw."""

    def register_commands(self, group: click.Group) -> None:
        """Register WhatsApp CLI commands."""
        import asyncio

        @group.group()
        def whatsapp() -> None:
            """WhatsApp integration commands."""
            pass

        @whatsapp.command()
        def auth() -> None:
            """Authenticate with WhatsApp using QR code."""
            from .auth import run_auth

            asyncio.run(run_auth())

        @whatsapp.command()
        def run() -> None:
            """Run WhatsApp message listener."""
            click.echo("WhatsApp listener not yet implemented")

        @whatsapp.command()
        def status() -> None:
            """Check WhatsApp connection status."""
            click.echo("WhatsApp status check not yet implemented")

    def get_db_migrations(self) -> list[str]:
        """Return SQL migrations for WhatsApp tables."""
        return [
            """
            CREATE TABLE IF NOT EXISTS wa_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_jid TEXT NOT NULL,
                sender TEXT,
                text TEXT,
                timestamp TEXT NOT NULL,
                is_from_me INTEGER DEFAULT 0
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS wa_chats (
                jid TEXT PRIMARY KEY,
                name TEXT,
                last_timestamp TEXT,
                last_agent_timestamp TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS wa_config (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """,
        ]

    def get_config_class(self) -> type[BaseSettings] | None:
        """Return WhatsApp configuration class."""
        return WhatsAppSettings
