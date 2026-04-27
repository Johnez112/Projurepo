# Distributed Chat System

A real-time distributed chat platform. The system consists of four independent microservices communicating via XML-RPC and TCP sockets. Users access the chat through a browser with no installation required.

---

## Architecture

```
Browser  ──HTTP/REST──►  [Web Gateway :5000]  ──XML-RPC──►  [Auth Service    :8001]
                                               ──XML-RPC──►  [History Service :8002]
                         [Web Gateway :5000]  ──TCP──────►  [Chat Service    :12345]
                         [Chat Service :12345] ──XML-RPC──►  [Auth Service    :8001]
                                               ──XML-RPC──►  [History Service :8002]
```

The browser never communicates directly with the Chat Service. The Web Gateway opens a TCP connection on the server side and streams messages to the browser via Server-Sent Events (SSE).

---

## Project Structure

```
Projurepo/
├── services/
│   ├── auth_service.py      — User registration, login, token validation (XML-RPC)
│   ├── history_service.py   — Message storage and retrieval (XML-RPC + SQLite)
│   ├── chat_service.py      — Real-time TCP chat, channels, users
│   ├── web_gateway.py       — REST API + SSE bridge between browser and Chat Service
│   └── config.py            — Shared configuration (ports, hosts, limits)
├── templates/
│   └── index.html           — Frontend HTML
├── static/
│   ├── app.js               — Frontend logic and backend communication
│   └── style.css            — Frontend styles
├── data/                    — SQLite databases (auto-created on first run, not in git)
│   ├── .gitkeep
│   ├── auth.db              — created automatically on first run
│   └── history.db           — created automatically on first run
├── start_services.py        — Starts all services in the correct order
├── requirements.txt
└── README.md
```

---

## Technologies

| Component | Technology | Reason |
|---|---|---|
| Auth Service | XML-RPC + SQLite | Standard library, no extra installs. PBKDF2 password hashing. |
| History Service | XML-RPC + SQLite | Same RPC mechanism demonstrates microservice independence. |
| Chat Service | TCP Sockets + Threading | One thread per user, fast direct connection without HTTP overhead. |
| Web Gateway | Flask (REST + SSE) | Single public entry point. SSE enables real-time updates without WebSockets. |
| Frontend | HTML / CSS / JavaScript | No installation needed, works in any browser. |

---

## Setup and Running

### Requirements
- Python 3.10 or newer

### Install dependencies

```bash
pip install flask
```

### Start all services

```bash
python start_services.py
```

Wait until you see: `Running on http://127.0.0.1:5000`

### Open in browser

```
http://127.0.0.1:5000
```

Multiple users can join by opening the same URL in different browser tabs. On a local network, use the host machine's IP address, e.g. `http://192.168.1.x:5000`.

---

## Features

- User registration and login with session tokens
- Real-time messaging via SSE (no page reloads)
- Multiple channels — create new channels by joining them
- Message history loaded on channel join
- Online users list per channel
- Private messaging with `/pm`
- Health check endpoint for all services

## Chat Commands

| Command | Example | Description |
|---|---|---|
| `/join <channel>` | `/join general` | Switch to or create a channel |
| `/channels` | `/channels` | List all active channels |
| `/users` | `/users` | List users in current channel |
| `/history [n]` | `/history 30` | Show last n messages (default 20) |
| `/pm <user> <msg>` | `/pm Alice hey!` | Send a private message |
| `/help` | `/help` | Show all commands |
| `/quit` | `/quit` | Disconnect |

---

## REST API

```bash
# Health check
curl http://localhost:5000/api/health

# Register
curl -X POST http://localhost:5000/api/register \
     -H "Content-Type: application/json" \
     -d '{"username":"alice","password":"pass123"}'

# Login
curl -X POST http://localhost:5000/api/login \
     -H "Content-Type: application/json" \
     -d '{"username":"alice","password":"pass123"}'

# Channel history
curl http://localhost:5000/api/history/general?limit=20

# List channels
curl http://localhost:5000/api/channels

# System stats
curl http://localhost:5000/api/stats
```

---

## Databases

Databases are created automatically in the `data/` folder on first run and are excluded from version control.

| File | Service | Contents |
|---|---|---|
| `data/auth.db` | Auth Service | `users` table (username, password hash, salt) + `tokens` table |
| `data/history.db` | History Service | `messages` table (channel, username, message, timestamp) |

---

## Fault Tolerance

- If the Auth Service restarts, already connected users continue chatting — only new logins are affected
- If the History Service goes down, messages still deliver in real-time — only saving and history retrieval are affected
- If a user closes the browser, the Chat Service detects the broken socket and notifies other users automatically
- `/api/health` checks all services and returns `"overall": "degraded"` if any service is unavailable

