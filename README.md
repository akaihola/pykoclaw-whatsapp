# pykoclaw-whatsapp

WhatsApp integration plugin for [pykoclaw](https://github.com/akaihola/pykoclaw). Connects a Claude
agent to WhatsApp using [Neonize](https://github.com/krypton-byte/neonize) (a
Python wrapper for the whatsmeow Go library). The agent listens for messages,
responds when triggered, and can proactively send messages via MCP tools.

## Setup

### 1. Authenticate

```bash
pykoclaw whatsapp auth
```

A QR code is displayed in the terminal. Scan it with WhatsApp on your phone
(Settings > Linked Devices > Link a Device). Credentials are saved to
`~/.pykoclaw/whatsapp/auth/`.

### 2. Run the listener

```bash
pykoclaw whatsapp run
```

This starts a long-running process that listens for incoming WhatsApp messages.

## How conversations work

Unlike `pykoclaw chat` where you choose a conversation name, the WhatsApp plugin
identifies conversations automatically from the WhatsApp chat JID:

| Chat type      | JID format               | Conversation name           |
| -------------- | ------------------------ | --------------------------- |
| Direct message | `<phone>@s.whatsapp.net` | `wa-<phone>@s.whatsapp.net` |
| Group          | `<id>@g.us`              | `wa-<id>@g.us`              |

Each conversation gets its own directory at
`~/.local/share/pykoclaw/conversations/wa-<jid>/`.

### Triggering the agent

Not every message invokes the agent:

- **In self-chat** (messages to yourself): The agent always responds.
- **In all other chats**: The message must contain `@Andy` (or whatever
  `PYKOCLAW_WA_TRIGGER_NAME` is set to).

### Context continuity

The WhatsApp plugin does not use Claude SDK session resumption. Instead, it
maintains its own message history:

1. All incoming and outgoing messages are stored in the `wa_messages` table.
2. A per-chat cursor (`last_agent_timestamp`) tracks which messages the agent
   has already seen.
3. When triggered, all new messages since the last agent response are fetched,
   formatted as XML, and included in the prompt.

This gives the agent a sliding window of conversation context on each
invocation.

## Configuration

| Variable                   | Default                           | Description                            |
| -------------------------- | --------------------------------- | -------------------------------------- |
| `PYKOCLAW_WA_AUTH_DIR`     | `~/.pykoclaw/whatsapp/auth`       | WhatsApp auth credentials directory    |
| `PYKOCLAW_WA_TRIGGER_NAME` | `Andy`                            | Trigger name for `@mention` activation |
| `PYKOCLAW_WA_SESSION_DB`   | `~/.pykoclaw/whatsapp/session.db` | Neonize session database path          |

Core settings (`PYKOCLAW_DATA`, `PYKOCLAW_MODEL`) also apply. See the
[pykoclaw README](https://github.com/akaihola/pykoclaw).

## MCP tools

The agent has access to two WhatsApp-specific tools:

| Tool               | Description                              |
| ------------------ | ---------------------------------------- |
| `send_message`     | Send a WhatsApp message to a chat by JID |
| `get_chat_history` | Retrieve unprocessed messages for a chat |

These are available alongside the core pykoclaw tools (task scheduling, etc.).

## Architecture

```
pykoclaw whatsapp run
  └── WhatsAppConnection
       ├── Neonize client (Go threads)
       │    ├── ConnectedEv    → flush outgoing queue
       │    ├── DisconnectedEv → buffer outgoing messages
       │    └── MessageEv      → MessageHandler.on_message()
       │         ├── extract text (plain, extended, captions)
       │         ├── store in wa_messages
       │         ├── check should_trigger() (@Andy or self-chat)
       │         └── bridge to asyncio → _handle_agent_trigger()
       │              ├── fetch new messages (dual-cursor)
       │              ├── format as XML context
       │              ├── query_agent() → Claude response
       │              └── send reply via OutgoingQueue
       └── OutgoingQueue (buffers on disconnect, flushes on reconnect)
```

### Threading model

Three threads share a single SQLite connection:

| Thread             | Created by                  | Runs                                         |
| ------------------ | --------------------------- | -------------------------------------------- |
| Main               | Python                      | `neonize.connect()` (blocks)                 |
| Go callback        | Neonize/whatsmeow           | `on_message()` → `store_message`, `update_*` |
| asyncio event loop | `threading.Thread` (daemon) | `_handle_agent_trigger` → `query_agent`      |

The connection is created on the main thread but used from all three. Python's
`sqlite3` C extension releases the GIL during `sqlite3_step()`, so concurrent
access on the same connection can corrupt internal state. To prevent this,
`init_db()` returns a `ThreadSafeConnection` wrapper that serializes all access
with a `threading.Lock`.

**Future alternatives** if lock contention becomes a bottleneck:

- **Connection-per-thread** — each thread opens its own connection; WAL mode
  allows parallel reads. Requires passing a factory instead of a connection.
- **Connection pool** (`sqlite3` + `queue.Queue`) — fixed pool of N
  connections, threads borrow and return.
- **aiosqlite** — async wrapper with a dedicated writer thread; natural fit for
  the asyncio side but the Go callback thread still needs synchronous access.

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

Or with `uv pip install`:

```bash
uv pip install pykoclaw@git+https://github.com/akaihola/pykoclaw.git
uv pip install pykoclaw-whatsapp@git+https://github.com/akaihola/pykoclaw-whatsapp.git
```

See the [pykoclaw README](https://github.com/akaihola/pykoclaw) for more
details.
