"""WhatsApp multi-agent group routing configuration.

Maps WhatsApp group JIDs to one or more agent personalities. Each agent has
a trigger name (for @mentions) and an optional model override. Groups not
listed in the routes table use the default agent.

Config file format (JSON)::

    {
        "default_agent": "Ressu",
        "agents": {
            "Ressu": {},
            "Tyko":  {"model": "claude-opus-4-6"}
        },
        "routes": {
            "120363...@g.us": ["Ressu"],
            "120364...@g.us": ["Tyko"],
            "120365...@g.us": ["Ressu", "Tyko"]
        }
    }
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class AgentConfig:
    """Configuration for a single agent personality."""

    name: str
    model: str | None = None


@dataclass
class RoutingConfig:
    """Multi-agent group routing configuration."""

    default_agent: str
    agents: dict[str, AgentConfig] = field(default_factory=dict)
    routes: dict[str, list[str]] = field(default_factory=dict)

    def agents_for_chat(self, chat_jid: str) -> list[AgentConfig]:
        """Return the agent(s) mapped to a chat JID.

        Groups not in the routing table and all DMs use the default agent.
        """
        if chat_jid in self.routes:
            return [self.agents[name] for name in self.routes[chat_jid]]
        return [self.agents[self.default_agent]]

    def is_multi_agent(self, chat_jid: str) -> bool:
        """Return True if the chat has multiple agents mapped."""
        return chat_jid in self.routes and len(self.routes[chat_jid]) > 1

    @property
    def all_trigger_names(self) -> list[str]:
        """Return all agent names (for hard mention detection)."""
        return [a.name for a in self.agents.values()]

    def conversation_name(self, agent: AgentConfig, chat_jid: str) -> str:
        """Build the conversation name for an agent + chat pair.

        Format: ``wa-{agent_name_lower}-{jid}``
        """
        return f"wa-{agent.name.lower()}-{chat_jid}"

    def parse_conversation(self, conversation: str) -> tuple[AgentConfig | None, str]:
        """Parse a conversation name back into (agent, chat_jid).

        Returns ``(None, "")`` if the conversation doesn't match any known agent.
        """
        for agent in self.agents.values():
            prefix = f"wa-{agent.name.lower()}-"
            if conversation.startswith(prefix):
                return agent, conversation[len(prefix) :]
        return None, ""


def load_routing_config(path: Path | None, default_trigger: str) -> RoutingConfig:
    """Load routing config from JSON file, or create a single-agent default.

    Args:
        path: Path to the JSON config file, or None for single-agent mode.
        default_trigger: Fallback agent name (from ``PYKOCLAW_WA_TRIGGER_NAME``).

    Returns:
        A :class:`RoutingConfig` instance.
    """
    if path is None or not path.exists():
        agent = AgentConfig(name=default_trigger)
        return RoutingConfig(
            default_agent=default_trigger,
            agents={default_trigger: agent},
        )

    with open(path) as f:
        data = json.load(f)

    agents: dict[str, AgentConfig] = {}
    for name, cfg in data.get("agents", {}).items():
        agents[name] = AgentConfig(
            name=name,
            model=cfg.get("model"),
        )

    default_name = data.get("default_agent", default_trigger)
    if default_name not in agents:
        agents[default_name] = AgentConfig(name=default_name)

    routes: dict[str, list[str]] = {}
    for jid, agent_names in data.get("routes", {}).items():
        valid_names = []
        for name in agent_names:
            if name in agents:
                valid_names.append(name)
            else:
                log.warning(
                    "Route %s references unknown agent %r — skipping", jid, name
                )
        if valid_names:
            routes[jid] = valid_names

    log.info(
        "Loaded routing config: %d agents, %d routes (default=%s)",
        len(agents),
        len(routes),
        default_name,
    )

    return RoutingConfig(
        default_agent=default_name,
        agents=agents,
        routes=routes,
    )
