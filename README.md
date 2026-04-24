# 🔬 Meta AI Reverse-Engineered CLI Client

> **Security Research Tool** — A reverse-engineered command-line interface for Meta AI, built through protocol-level analysis of the WebSocket communication layer.

⚠️ **Disclaimer**: This project is strictly for **educational and security research purposes**. It demonstrates reverse engineering techniques applied to a public-facing AI service. Use responsibly and in compliance with applicable terms of service.

---

## 📋 Overview

This tool captures, decodes, and replays Meta AI's WebSocket binary frames to enable programmatic interaction from a terminal. No official API exists for Meta AI — this client was built entirely through protocol analysis.

### How It Works

```
┌──────────┐     ┌──────────────┐     ┌────────────┐
│ Terminal  │────▶│ Playwright   │────▶│ meta.ai WS │
│ (You>)   │     │ CDP Protocol │     │ gateway    │
│          │◀────│ Frame Replay │◀────│            │
└──────────┘     └──────────────┘     └────────────┘
```

1. **Capture** — Opens a headless browser, types a test message, and captures the raw WebSocket binary frame via Chrome DevTools Protocol (CDP)
2. **Decode** — Uses `blackboxprotobuf` to decode the protobuf payload without `.proto` files, locating the exact byte offset of the user's text
3. **Replay** — For each new prompt, clones the captured frame, replaces the text at the known offset, regenerates `req-id` UUIDs, recalculates binary length headers, and sends via the live WebSocket

### Key Features

| Feature | Description |
|---------|-------------|
| **Live Streaming** | Responses stream word-by-word to terminal in real-time |
| **Stealth Mode** | Auto-deletes conversations from Meta AI history on exit (enabled by default) |
| **Protobuf RE** | Dynamic schema detection — no hardcoded `.proto` files needed |
| **Frame Replay** | Binary-level frame manipulation with fresh UUIDs per request |
| **Multi-WS Dedup** | Handles Meta's multiple parallel WebSocket connections without duplicating output |
| **IE Tag Stripping** | Removes internal `{{IE_N}}` markup tags from responses |

---

## 🛠️ Installation

### Prerequisites

- Python 3.9+
- A Meta AI account (free, via google/Facebook/Instagram login)

### Setup

```bash
# Clone the repository
git clone https://github.com/Adithyan-Defender/meta-ai-websocket-re-client.git
cd meta-ai-websocket-re-client

# Install dependencies
pip install -r requirements.txt

# Install Playwright browser
playwright install chromium
```

### Dependencies

| Package | Purpose |
|---------|---------|
| `playwright` | Browser automation + CDP WebSocket interception |
| `blackboxprotobuf` | Protobuf decoding without `.proto` schema files |

---

## 🚀 Usage

### Step 1: Login (one-time)

Opens a visible browser window. Log into Meta AI with your account, then close the browser.

```bash
python meta_ai_websocket_client.py --login
```

Your browser profile is saved locally in `meta_profile/` for subsequent sessions.

### Step 2: Capture (one-time)

Captures a test WebSocket frame that serves as the template for all future messages.

```bash
python meta_ai_websocket_client.py --capture
```

This auto-types a test string, captures the binary frame, decodes the protobuf, and saves the field path for text injection.

### Step 3: Chat

```bash
# Interactive REPL mode
python meta_ai_websocket_client.py

# Single prompt mode
python meta_ai_websocket_client.py --prompt "explain quantum computing"
```

### Interactive Session Example

```
  ======================================================
   Meta AI RE Client v10 -- Stealth + Live Streaming
  ======================================================

  [+] Loaded capture (field: [2][2])
  [+] Ready. (stealth ON)

  You> what is reverse engineering?

  Reverse engineering is the process of analyzing a system,
  device, or software to understand its design, architecture,
  and functionality — essentially working backwards from the
  finished product to figure out how it was built...

  You> exit
```

### CLI Options

| Flag | Description |
|------|-------------|
| `--login` | Open browser for Meta AI authentication |
| `--capture` | Capture a fresh WebSocket message template |
| `--prompt "text"` | Send a single prompt and exit |
| `--no-stealth` | Keep conversations visible in Meta AI browser history |

---

## 🔒 Stealth Mode

By default, stealth mode is **enabled**. When you exit the CLI (type `exit`, `quit`, or press Ctrl+C):

1. Extracts all conversation IDs created during the session
2. Calls Meta's GraphQL API to delete them from your account history
3. Conversations will not appear in the Meta AI web interface

To disable: `python meta_ai_websocket_client.py --no-stealth`

---

## 🧪 Technical Deep Dive

### Reverse Engineering Methodology

**Phase 1 — Protocol Discovery**
- Used Playwright + CDP `Network.webSocketCreated/FrameSent/FrameReceived` events to intercept raw WebSocket traffic
- Identified binary framing: `[8-byte header][JSON envelope]` where the envelope contains `{"req-id": "...", "payload": "base64_protobuf"}`

**Phase 2 — Protobuf Decoding**
- Applied `blackboxprotobuf.decode_message()` against the raw payload to recover the schema dynamically
- Mapped field `[2][2]` as the user input text field across multiple captures

**Phase 3 — Frame Replay**
- Clone captured frame → replace text at known protobuf offset → regenerate `req-id` UUID → recalculate binary length header → send via CDP `Runtime.evaluate` → WebSocket `send()`

**Phase 4 — Response Parsing**
- Responses arrive as JSON `"patch"` operations with `"delta"` text fragments (streaming) or `"full"` complete responses
- Custom brace-counting JSON parser handles arbitrary nesting depth
- Deduplication via `seen_deltas` set handles multi-WS connection duplicates

### Architecture

```
meta_ai_websocket_client.py
├── CDP Setup          — WebSocket interception via Chrome DevTools Protocol
├── Capture Mode       — Auto-type test text, capture binary frame template
├── Protobuf Engine    — Decode, locate, and replace text in protobuf payloads
├── Frame Builder      — Clone frames with fresh UUIDs and recalculated headers
├── Send Engine        — JavaScript injection to send binary via live WebSocket
├── Response Parser    — Brace-counting JSON extraction + IE tag stripping
├── Recv Engine        — Real-time streaming with multi-WS deduplication
├── Stealth Cleanup    — GraphQL-based conversation deletion on exit
└── REPL               — Interactive prompt loop with streaming output
```

### Diagnostic Tool

`diagnose_meta.py` is included for protocol analysis. It captures HTTP requests, GraphQL mutations, and WebSocket connections for research purposes.

```bash
python diagnose_meta.py
```

---

## 📁 Project Structure

```
meta-ai-re-client/
├── meta_ai_websocket_client.py   # Main client (capture + replay + stealth)
├── diagnose_meta.py              # Protocol analysis / diagnostic tool
├── requirements.txt              # Python dependencies
├── .gitignore                    # Excludes credentials and session data
└── README.md                     # This file
```

### Generated at Runtime (git-ignored)

| File/Directory | Contents | Sensitive |
|----------------|----------|-----------|
| `meta_profile/` | Chromium browser profile with session cookies | **YES** |
| `captured_session.json` | Protobuf template frame with field paths | **YES** |
| `last_response.txt` | Last AI response cache | No |

---

## ⚠️ Security Warnings

1. **`meta_profile/`** contains your browser session cookies. Never commit or share this directory.
2. **`captured_session.json`** contains your account-specific protobuf payloads. Treat as credentials.
3. The `.gitignore` is pre-configured to exclude all sensitive files.
4. Run `--login` again if your session expires.

---

## 🔬 Research Context

This project demonstrates:
- **WebSocket binary protocol reverse engineering** without access to source code or documentation
- **Protobuf schema recovery** using dynamic decoding (no `.proto` files)
- **CDP-based browser instrumentation** for protocol interception
- **Frame replay attacks** with UUID regeneration to bypass replay protection
- **GraphQL API analysis** for session management operations

Built as part of an AI security research initiative exploring the attack surface of public-facing AI chat interfaces.

---

## 📄 License

This project is provided for educational and security research purposes only. No warranty is provided. Use at your own risk.
