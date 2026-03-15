"""
Distributed Chat System — Shared Configuration
All services read their addresses and ports from here.
"""

# --- Auth Service (XML-RPC) ---
AUTH_HOST = '127.0.0.1'
AUTH_PORT = 8001

# --- History Service (XML-RPC) ---
HISTORY_HOST = '127.0.0.1'
HISTORY_PORT = 8002

# --- Chat Service (TCP Sockets) ---
CHAT_HOST = '0.0.0.0'
CHAT_PORT = 12345

# --- API Gateway (Flask REST) ---
GATEWAY_HOST = '0.0.0.0'
GATEWAY_PORT = 5000

# --- Token expiry in seconds (1 hour) ---
TOKEN_EXPIRY_SECONDS = 3600

# --- Message history limit ---
DEFAULT_HISTORY_LIMIT = 50
