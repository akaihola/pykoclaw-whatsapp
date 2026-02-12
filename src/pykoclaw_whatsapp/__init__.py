"""WhatsApp plugin for pykoclaw."""

from __future__ import annotations

from textwrap import dedent
from typing import Any

import click
from pydantic_settings import BaseSettings

from pykoclaw.db import DbConnection
from pykoclaw.plugins import PykoClawPluginBase

from .config import WhatsAppSettings


class WhatsAppPlugin(PykoClawPluginBase):
    """WhatsApp plugin for pykoclaw."""

    def register_commands(self, group: click.Group) -> None:
        @group.group()
        def whatsapp() -> None:
            """WhatsApp integration commands."""

        @whatsapp.command()
        def auth() -> None:
            """Authenticate with WhatsApp using QR code."""
            from .auth import run_auth

            run_auth()

        @whatsapp.command()
        def run() -> None:
            """Run WhatsApp message listener."""
            from pykoclaw.config import settings
            from pykoclaw.db import init_db
            from pykoclaw.plugins import run_db_migrations

            from .connection import WhatsAppConnection

            db = init_db(settings.db_path)
            db.execute("PRAGMA journal_mode=WAL")

            plugin = WhatsAppPlugin()
            run_db_migrations(db, [plugin])

            mcp_servers = plugin.get_mcp_servers(db, "whatsapp")

            from .config import get_config

            wa_config = get_config()
            click.echo(f"Data directory: {settings.data}")
            click.echo(f"Trigger name:   {wa_config.trigger_name}")

            conn = WhatsAppConnection(db=db, extra_mcp_servers=mcp_servers)
            conn.run()

        @whatsapp.command()
        def status() -> None:
            """Check WhatsApp connection status."""
            click.echo("WhatsApp status check not yet implemented")

    def get_db_migrations(self) -> list[str]:
        return [
            dedent("""\
                CREATE TABLE IF NOT EXISTS wa_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_jid TEXT NOT NULL,
                    sender TEXT,
                    text TEXT,
                    timestamp TEXT NOT NULL,
                    is_from_me INTEGER DEFAULT 0
                )"""),
            dedent("""\
                CREATE TABLE IF NOT EXISTS wa_chats (
                    jid TEXT PRIMARY KEY,
                    name TEXT,
                    last_timestamp TEXT,
                    last_agent_timestamp TEXT
                )"""),
            dedent("""\
                CREATE TABLE IF NOT EXISTS wa_config (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )"""),
        ]

    def get_config_class(self) -> type[BaseSettings] | None:
        return WhatsAppSettings

    def get_mcp_servers(self, db: DbConnection, conversation: str) -> dict[str, Any]:
        from claude_agent_sdk import create_sdk_mcp_server, tool

        from .handler import format_xml_messages, get_new_messages_for_chat

        @tool(
            "send_message",
            dedent("""\
                Send a WhatsApp message to a chat.
                The chat_jid is in format 'number@s.whatsapp.net' for DMs
                or 'id@g.us' for groups."""),
            {"chat_jid": str, "text": str},
        )
        async def send_message(args: dict[str, Any]) -> dict[str, Any]:
            chat_jid = args["chat_jid"]
            text = args["text"]
            db.execute(
                dedent("""\
                    INSERT INTO wa_messages
                        (chat_jid, sender, text, timestamp, is_from_me)
                    VALUES (?, ?, ?, datetime('now'), 1)"""),
                (chat_jid, "assistant", text),
            )
            db.commit()
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Message queued for {chat_jid} ({len(text)} chars)",
                    }
                ]
            }

        @tool(
            "get_chat_history",
            "Get recent messages from a WhatsApp chat.",
            {"chat_jid": str},
        )
        async def get_chat_history(args: dict[str, Any]) -> dict[str, Any]:
            messages = get_new_messages_for_chat(db, args["chat_jid"])
            if not messages:
                return {"content": [{"type": "text", "text": "No new messages."}]}
            xml = format_xml_messages(messages)
            return {"content": [{"type": "text", "text": xml}]}

        return {
            "whatsapp": create_sdk_mcp_server(
                name="whatsapp",
                tools=[send_message, get_chat_history],
            )
        }
