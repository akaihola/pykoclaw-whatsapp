"""Tests for the WhatsApp plugin."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import click
import pytest

from pykoclaw_whatsapp import WhatsAppPlugin
from pykoclaw_whatsapp.config import WhatsAppSettings


def test_whatsapp_plugin_implements_protocol() -> None:
    """Test that WhatsAppPlugin implements PykoClawPlugin protocol."""
    from pykoclaw.plugins import PykoClawPlugin

    plugin = WhatsAppPlugin()
    assert isinstance(plugin, PykoClawPlugin)


def test_register_commands_adds_whatsapp_group() -> None:
    """Test that register_commands adds whatsapp command group."""
    plugin = WhatsAppPlugin()
    group = click.Group()

    plugin.register_commands(group)

    assert "whatsapp" in group.commands
    whatsapp_group = group.commands["whatsapp"]
    assert isinstance(whatsapp_group, click.Group)


def test_whatsapp_group_has_subcommands() -> None:
    """Test that whatsapp group has auth, run, and status subcommands."""
    plugin = WhatsAppPlugin()
    group = click.Group()

    plugin.register_commands(group)

    whatsapp_group = group.commands["whatsapp"]
    assert "auth" in whatsapp_group.commands
    assert "run" in whatsapp_group.commands
    assert "status" in whatsapp_group.commands


def test_get_db_migrations_returns_valid_sql() -> None:
    """Test that get_db_migrations returns valid SQL statements."""
    plugin = WhatsAppPlugin()
    migrations = plugin.get_db_migrations()

    assert len(migrations) == 3
    assert "CREATE TABLE IF NOT EXISTS wa_messages" in migrations[0]
    assert "CREATE TABLE IF NOT EXISTS wa_chats" in migrations[1]
    assert "CREATE TABLE IF NOT EXISTS wa_config" in migrations[2]

    db = sqlite3.connect(":memory:")
    for sql in migrations:
        db.executescript(sql)

    cursor = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row[0] for row in cursor.fetchall()]
    assert "wa_messages" in tables
    assert "wa_chats" in tables
    assert "wa_config" in tables


def test_get_config_class_returns_whatsapp_settings() -> None:
    """Test that get_config_class returns WhatsAppSettings."""
    plugin = WhatsAppPlugin()
    config_cls = plugin.get_config_class()

    assert config_cls is not None
    assert config_cls is WhatsAppSettings


def test_get_mcp_servers_returns_whatsapp_server() -> None:
    """Test that get_mcp_servers returns whatsapp MCP server."""
    pytest.importorskip("neonize")

    plugin = WhatsAppPlugin()
    db = sqlite3.connect(":memory:")

    servers = plugin.get_mcp_servers(db, "test")

    assert "whatsapp" in servers
    assert isinstance(servers["whatsapp"], dict)


def test_whatsapp_settings_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test WhatsAppSettings default values."""
    monkeypatch.chdir(tmp_path)

    settings = WhatsAppSettings()

    assert settings.trigger_name == "Andy"
    assert "whatsapp" in str(settings.auth_dir)
    assert "session.db" in str(settings.session_db)
