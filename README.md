# Distributed Chat System

A microservice-based real-time chat platform built for a Distributed Systems course.

## Architecture

```
[Client]  ──REST──►  [API Gateway :5000]  ──XML-RPC──►  [Auth Service :8001]
                                           ──XML-RPC──►  [History Service :8002]
[Client]  ──TCP────►  [Chat Service :12345] ──XML-RPC──►  [Auth Service :8001]
                                             ──XML-RPC──►  [History Service :8002]
```

## Technologies

| Component       | Technology          | Purpose                         |
|----------------|---------------------|---------------------------------|
| Auth Service    | Python XML-RPC      | User registration, login, tokens |
| History Service | Python XML-RPC      | Persistent message storage       |
| Chat Service    | TCP Sockets + Threads| Real-time messaging             |
| API Gateway     | Flask REST          | Public HTTP API                  |
| Client          | TCP + urllib (REST) | Terminal user interface          |
| Databases       | SQLite              | auth.db, history.db              |

## Installation

```bash
pip install flask
```

## Starting the system

Open **4 terminal windows** and run each service in order:

```bash
# Terminal 1 — Auth Service
python auth_service.py

# Terminal 2 — History Service
python history_service.py

# Terminal 3 — Chat Service
python chat_service.py

# Terminal 4 — API Gateway
python gateway.py
```

Then start one or more clients:

```bash
python chat_client.py
```

## Chat Commands

| Command             | Description                    |
|---------------------|-------------------------------|
| `/help`             | Show all commands              |
| `/join <channel>`   | Switch to a channel            |
| `/channels`         | List all active channels       |
| `/users`            | List users in current channel  |
| `/history [n]`      | Show last n messages           |
| `/pm <user> <msg>`  | Send a private message         |
| `/quit`             | Disconnect                     |

## REST API

```bash
# Register
curl -X POST http://localhost:5000/api/register \
     -H "Content-Type: application/json" \
     -d '{"username":"alice","password":"1234"}'

# Login
curl -X POST http://localhost:5000/api/login \
     -H "Content-Type: application/json" \
     -d '{"username":"alice","password":"1234"}'

# Message history
curl http://localhost:5000/api/history/general?limit=20

# All channels
curl http://localhost:5000/api/channels

# Health check
curl http://localhost:5000/api/health
```
