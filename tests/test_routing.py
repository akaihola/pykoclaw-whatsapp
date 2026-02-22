"""Tests for WhatsApp multi-agent group routing."""

from __future__ import annotations

import json
from pathlib import Path

from pykoclaw_whatsapp.routing import (
    AgentConfig,
    RoutingConfig,
    load_routing_config,
)


# --- RoutingConfig unit tests ---


def _make_config() -> RoutingConfig:
    """Create a test routing config with two agents and mixed routes."""
    return RoutingConfig(
        default_agent="Ressu",
        agents={
            "Ressu": AgentConfig(name="Ressu"),
            "Tyko": AgentConfig(name="Tyko", model="claude-opus-4-6"),
        },
        routes={
            "group-single@g.us": ["Ressu"],
            "group-tyko@g.us": ["Tyko"],
            "group-multi@g.us": ["Ressu", "Tyko"],
        },
    )


def test_agents_for_chat_default() -> None:
    """Unrouted chats use the default agent."""
    cfg = _make_config()
    agents = cfg.agents_for_chat("unknown@s.whatsapp.net")
    assert len(agents) == 1
    assert agents[0].name == "Ressu"


def test_agents_for_chat_single_route() -> None:
    """Single-agent route returns one agent."""
    cfg = _make_config()
    agents = cfg.agents_for_chat("group-tyko@g.us")
    assert len(agents) == 1
    assert agents[0].name == "Tyko"
    assert agents[0].model == "claude-opus-4-6"


def test_agents_for_chat_multi_route() -> None:
    """Multi-agent route returns both agents in order."""
    cfg = _make_config()
    agents = cfg.agents_for_chat("group-multi@g.us")
    assert len(agents) == 2
    assert agents[0].name == "Ressu"
    assert agents[1].name == "Tyko"


def test_is_multi_agent() -> None:
    cfg = _make_config()
    assert not cfg.is_multi_agent("unknown@s.whatsapp.net")
    assert not cfg.is_multi_agent("group-single@g.us")
    assert not cfg.is_multi_agent("group-tyko@g.us")
    assert cfg.is_multi_agent("group-multi@g.us")


def test_all_trigger_names() -> None:
    cfg = _make_config()
    names = cfg.all_trigger_names
    assert "Ressu" in names
    assert "Tyko" in names


def test_conversation_name() -> None:
    cfg = _make_config()
    ressu = cfg.agents["Ressu"]
    tyko = cfg.agents["Tyko"]

    assert cfg.conversation_name(ressu, "123@g.us") == "wa-ressu-123@g.us"
    assert cfg.conversation_name(tyko, "123@g.us") == "wa-tyko-123@g.us"


def test_parse_conversation() -> None:
    cfg = _make_config()

    agent, jid = cfg.parse_conversation("wa-ressu-123@g.us")
    assert agent is not None
    assert agent.name == "Ressu"
    assert jid == "123@g.us"

    agent, jid = cfg.parse_conversation("wa-tyko-456@s.whatsapp.net")
    assert agent is not None
    assert agent.name == "Tyko"
    assert jid == "456@s.whatsapp.net"


def test_parse_conversation_unknown() -> None:
    cfg = _make_config()
    agent, jid = cfg.parse_conversation("wa-unknown-123@g.us")
    assert agent is None
    assert jid == ""


# --- load_routing_config tests ---


def test_load_no_file_returns_default() -> None:
    """When no file is given, creates a single-agent config."""
    cfg = load_routing_config(None, "Andy")
    assert cfg.default_agent == "Andy"
    assert len(cfg.agents) == 1
    assert "Andy" in cfg.agents
    assert len(cfg.routes) == 0


def test_load_missing_file_returns_default(tmp_path: Path) -> None:
    """When file doesn't exist, creates a single-agent config."""
    cfg = load_routing_config(tmp_path / "nonexistent.json", "Andy")
    assert cfg.default_agent == "Andy"
    assert len(cfg.agents) == 1


def test_load_from_json(tmp_path: Path) -> None:
    """Load a full routing config from JSON."""
    config_data = {
        "default_agent": "Ressu",
        "agents": {
            "Ressu": {},
            "Tyko": {"model": "claude-opus-4-6"},
        },
        "routes": {
            "120363@g.us": ["Ressu"],
            "120364@g.us": ["Tyko"],
            "120365@g.us": ["Ressu", "Tyko"],
        },
    }
    config_file = tmp_path / "routes.json"
    config_file.write_text(json.dumps(config_data))

    cfg = load_routing_config(config_file, "Fallback")

    assert cfg.default_agent == "Ressu"
    assert len(cfg.agents) == 2
    assert cfg.agents["Tyko"].model == "claude-opus-4-6"
    assert len(cfg.routes) == 3
    assert cfg.routes["120365@g.us"] == ["Ressu", "Tyko"]


def test_load_adds_default_agent_if_missing(tmp_path: Path) -> None:
    """If default_agent isn't in agents dict, it's added automatically."""
    config_data = {
        "default_agent": "Ressu",
        "agents": {},
        "routes": {},
    }
    config_file = tmp_path / "routes.json"
    config_file.write_text(json.dumps(config_data))

    cfg = load_routing_config(config_file, "Fallback")
    assert "Ressu" in cfg.agents


def test_load_skips_unknown_agents_in_routes(tmp_path: Path) -> None:
    """Routes referencing unknown agents are dropped with a warning."""
    config_data = {
        "default_agent": "Ressu",
        "agents": {"Ressu": {}},
        "routes": {
            "good-group@g.us": ["Ressu"],
            "bad-group@g.us": ["NonExistent"],
        },
    }
    config_file = tmp_path / "routes.json"
    config_file.write_text(json.dumps(config_data))

    cfg = load_routing_config(config_file, "Fallback")

    assert "good-group@g.us" in cfg.routes
    # bad-group only had NonExistent → all agents stripped → route dropped
    assert "bad-group@g.us" not in cfg.routes


# --- find_hard_mentions integration ---


def test_find_hard_mentions_multi_agent() -> None:
    __import__("pytest").importorskip("neonize")
    from pykoclaw_whatsapp.handler import find_hard_mentions

    mentioned = find_hard_mentions("@Tyko what do you think?", ["Ressu", "Tyko"])
    assert mentioned == {"Tyko"}

    mentioned = find_hard_mentions("@Ressu and @Tyko check this", ["Ressu", "Tyko"])
    assert mentioned == {"Ressu", "Tyko"}

    mentioned = find_hard_mentions("Hello everyone", ["Ressu", "Tyko"])
    assert mentioned == set()
