# pykoclaw-whatsapp

WhatsApp integration plugin for [pykoclaw][pykoclaw]. Connects one or more
Claude agent personalities to WhatsApp using [Neonize][neonize] (a Python
wrapper for the whatsmeow Go library). Agents observe conversations silently,
respond when addressed, and can send proactive messages via MCP tools and
scheduled task delivery.

## Setup

### 1. Authenticate

```bash
pykoclaw whatsapp auth
```

A QR code is displayed in the terminal. Scan it with WhatsApp on your phone
(Settings → Linked Devices → Link a Device). Credentials are saved to the
configured auth directory.

### 2. Run the listener

```bash
pykoclaw whatsapp run
```

This starts a long-running process that connects to WhatsApp, listens for
messages, and dispatches them to the configured agent(s).

### 3. Run as a systemd service (recommended)

```ini
[Unit]
Description=Pykoclaw WhatsApp listener (neonize)
After=network.target

[Service]
Environment=PYKOCLAW_DATA=/home/user/my-agent
Environment=PYKOCLAW_WA_TRIGGER_NAME=Andy
Environment=LD_LIBRARY_PATH=/run/current-system/sw/share/nix-ld/lib
ExecStart=/home/user/.local/bin/pykoclaw whatsapp run
Restart=always
RestartSec=10
StandardOutput=append:/home/user/.local/state/pykoclaw/whatsapp.log
StandardError=append:/home/user/.local/state/pykoclaw/whatsapp.log

[Install]
WantedBy=default.target
```

## Ambient participation model

The agent operates as an **ambient participant** in every chat. Instead of
responding to every message, it:

1. **Accumulates** incoming messages in per-chat batches.
2. After a configurable **batch window** (default 90 s), sends the batch to the
   LLM with instructions to decide whether to reply.
3. **Hard mentions** (`@AgentName` or the agent's name at the start of a
   sentence) flush the batch immediately and instruct the LLM that it MUST
   reply.
4. Replies must be wrapped in `<reply>` tags — text outside the tags is treated
   as internal monologue and discarded.

This means the agent errs heavily toward silence and only speaks when addressed
or when it has genuinely useful information.

## Multi-agent group routing

A single WhatsApp account can serve **multiple agent personalities** across
different groups. Each group maps to one or more agents via a JSON routing
config.

### Setting up multi-agent routing

#### Step 1: Create agent data directories

Each agent needs its own data directory with a `CLAUDE.md`, `.claude/`
settings, and its own `pykoclaw.db`:

```
~/pipsa/          # Ressu's data
~/my-knowledge/   # Tyko's data
~/paivi/          # Väinö's data
```

#### Step 2: Create the routing config

Create a JSON file (e.g. `agent-routes.json`) in the bridge's data directory:

```json
{
    "default_agent": "Ressu",
    "agents": {
        "Ressu": {
            "data_dir": "/home/user/pipsa",
            "model": "claude-sonnet-4-6"
        },
        "Tyko": {
            "data_dir": "/home/user/my-knowledge",
            "model": "claude-sonnet-4-6"
        },
        "Väinö": {
            "data_dir": "/home/user/paivi",
            "model": "claude-sonnet-4-6"
        }
    },
    "routes": {
        "120363406148723745@g.us": ["Ressu"],
        "120363407060889798@g.us": ["Tyko"],
        "120363424040407722@g.us": ["Väinö"]
    }
}
```

**Agent fields:**

| Field      | Required | Description                                        |
| ---------- | -------- | -------------------------------------------------- |
| `data_dir` | No       | Agent's data directory (DB, conversations, tools)   |
| `model`    | No       | Claude model override (e.g. `claude-sonnet-4-6`)   |

**Routing rules:**

- Groups listed in `routes` dispatch to the specified agent(s).
- Groups not listed and all DMs fall back to `default_agent`.
- A group can have multiple agents (see [Multi-agent groups][multi-agent] below).

#### Step 3: Find group JIDs

Group JIDs look like `120363406148723745@g.us`. To find a group's JID:

1. Start the WhatsApp listener without routing (or with a partial config).
2. Add the bot's phone number to the WhatsApp group.
3. Send a message in the group.
4. Look up the JID in the database:

```bash
sqlite3 /path/to/pykoclaw.db \
  "SELECT jid, last_timestamp FROM wa_chats ORDER BY last_timestamp DESC"
```

Or check the most recent messages to identify which JID is which group:

```bash
sqlite3 /path/to/pykoclaw.db \
  "SELECT chat_jid, sender, text FROM wa_messages ORDER BY timestamp DESC LIMIT 10"
```

#### Step 4: Set the environment variable

Point the listener to the routing config via `PYKOCLAW_WA_AGENT_ROUTES`:

```bash
export PYKOCLAW_WA_AGENT_ROUTES=/home/user/pipsa/agent-routes.json
pykoclaw whatsapp run
```

Or in the systemd service:

```ini
Environment=PYKOCLAW_WA_AGENT_ROUTES=/home/user/pipsa/agent-routes.json
```

Then restart:

```bash
systemctl --user daemon-reload
systemctl --user restart pykoclaw-whatsapp
```

Verify in the logs:

```
Loaded routing config: 3 agents, 3 routes (default=Ressu)
Agents:         Ressu, Tyko, Väinö
Group routes:   3
  120363406148723745@g.us → Ressu
  120363407060889798@g.us → Tyko
  120363424040407722@g.us → Väinö
```

### Multi-agent groups

A group can have multiple agents:

```json
"routes": {
    "120365...@g.us": ["Ressu", "Tyko"]
}
```

In multi-agent groups:

- **Message prefixing:** Outgoing messages are prefixed with `[AgentName]: `
  so humans can tell which agent is speaking.
- **Hard mention routing:** `@Tyko what do you think?` only flags Tyko's
  dispatch as a must-reply — other agents in the group process the batch
  normally (and will likely stay silent).
- **Loop prevention:** Each agent's system prompt includes instructions to
  never respond to another agent's messages. Only human messages re-enable
  responses.
- **Sequential dispatch:** Agents process each batch one at a time to avoid
  resource contention.

### Per-agent isolation

Each agent with a `data_dir` gets:

- Its own **SQLite database** (conversations, scheduled tasks, session IDs)
- Its own **conversation working directories** (with agent-specific `CLAUDE.md`,
  `.claude/` settings)
- Its own **MCP tools** scoped to the agent's DB

The WhatsApp bridge's DB (`wa_messages`, `wa_chats`, `wa_config`) is shared
across all agents — it stores the raw message history that all agents read from.

### Backward compatibility

Without `PYKOCLAW_WA_AGENT_ROUTES`, the plugin behaves exactly as before: a
single agent using `PYKOCLAW_WA_TRIGGER_NAME` and `PYKOCLAW_DATA`.

## Conversations

The WhatsApp plugin identifies conversations from the chat JID and agent name:

| Chat type      | JID format               | Conversation name                    |
| -------------- | ------------------------ | ------------------------------------ |
| Direct message | `<phone>@s.whatsapp.net` | `wa-<agent>-<phone>@s.whatsapp.net`  |
| Group          | `<id>@g.us`              | `wa-<agent>-<id>@g.us`              |

Each conversation gets its own directory at
`<agent_data_dir>/conversations/wa-<agent>-<jid>/`.

## Configuration

| Variable                       | Default                           | Description                               |
| ------------------------------ | --------------------------------- | ----------------------------------------- |
| `PYKOCLAW_WA_AUTH_DIR`         | `~/.local/share/pykoclaw/whatsapp/auth`       | WhatsApp auth credentials directory       |
| `PYKOCLAW_WA_TRIGGER_NAME`     | `Andy`                            | Default agent name for @mention detection |
| `PYKOCLAW_WA_SESSION_DB`       | `~/.local/share/pykoclaw/whatsapp/session.db` | Neonize session database path             |
| `PYKOCLAW_WA_BATCH_WINDOW_SECONDS` | `90`                          | Seconds to accumulate messages before dispatch |
| `PYKOCLAW_WA_AGENT_ROUTES`     | *(none)*                          | Path to multi-agent routing JSON file     |

Core settings (`PYKOCLAW_DATA`, `PYKOCLAW_MODEL`) also apply. See the
[pykoclaw README][pykoclaw].

## MCP tools

The agent has access to two WhatsApp-specific tools:

| Tool               | Description                              |
| ------------------ | ---------------------------------------- |
| `send_message`     | Send a WhatsApp message to a chat by JID |
| `get_chat_history` | Retrieve unprocessed messages for a chat |

These are available alongside the core pykoclaw tools (task scheduling, etc.).

## Delivery queue

When the pykoclaw scheduler runs a scheduled task, results are written to a
`delivery_queue` table in SQLite. The WhatsApp plugin polls this queue every 10
seconds for items with channel prefix `wa`, then delivers them via
`OutgoingQueue.send()` through the live Neonize connection. In multi-agent
groups, delivered messages are prefixed with the agent's name.

## Architecture

```
pykoclaw whatsapp run
  └── WhatsAppConnection
       ├── RoutingConfig (JSON → agent/group mapping)
       ├── Per-agent DB connections (lazy init)
       ├── Neonize client (Go threads)
       │    ├── ConnectedEv    → flush outgoing queue
       │    ├── DisconnectedEv → buffer outgoing messages
       │    └── MessageEv      → MessageHandler.on_message()
       │         ├── extract text (plain, extended, captions)
       │         ├── store in wa_messages (bridge DB)
       │         ├── check hard mentions (all agent names)
       │         └── BatchAccumulator (per-chat timer)
       │              └── _handle_agent_trigger()
       │                   ├── look up agents for chat JID
       │                   ├── for each agent (sequential):
       │                   │    ├── build system prompt (multi-agent aware)
       │                   │    ├── dispatch_to_agent() (agent's DB + data_dir)
       │                   │    ├── extract <reply> tags
       │                   │    └── prefix + send via OutgoingQueue
       │                   └── advance per-chat cursor
       └── Delivery poll loop (10s interval)
```

### Threading model

Three threads share the bridge's SQLite connection (via `ThreadSafeConnection`):

| Thread             | Created by                  | Runs                                         |
| ------------------ | --------------------------- | -------------------------------------------- |
| Main               | Python                      | `neonize.connect()` (blocks)                 |
| Go callback        | Neonize/whatsmeow           | `on_message()` → `store_message`, `update_*` |
| asyncio event loop | `threading.Thread` (daemon) | `_handle_agent_trigger` → `query_agent`      |

Per-agent DBs are only accessed from the asyncio event loop thread.

### Neonize quirks

- `info.Timestamp` is in **milliseconds**, not seconds — divide by 1000 for
  `datetime.fromtimestamp()`.
- `client.me` is not a JID — use `client.me.JID` to get the JID object that
  `Jid2String()` expects.

## Supported message types

Text is extracted from:

- Plain text messages
- Extended text messages (e.g., with link previews)
- Image, video, and document captions

Other message types (audio, stickers, etc.) are silently ignored.

## Installation

```bash
uv tool install pykoclaw@git+https://github.com/akaihola/pykoclaw.git \
    --with=pykoclaw-whatsapp@git+https://github.com/akaihola/pykoclaw-whatsapp.git
```

See the [pykoclaw README][pykoclaw] for more details.

[multi-agent]: #multi-agent-groups
[neonize]: https://github.com/krypton-byte/neonize
[pykoclaw]: https://github.com/akaihola/pykoclaw
