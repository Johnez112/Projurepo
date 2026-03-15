"""
Auth Service  —  XML-RPC Microservice
Distributed Chat System

Handles:
  - User registration (username + hashed password stored in SQLite)
  - Login  (returns a session token)
  - Token validation  (used by Chat Service and Gateway)
  - Logout  (invalidates token)

Run:  python auth_service.py
Port: 8001  (configured in config.py)
"""

import sqlite3
import hashlib
import hmac
import os
import uuid
import time
import threading
from xmlrpc.server import SimpleXMLRPCServer, SimpleXMLRPCRequestHandler
from xmlrpc.client import Fault

import config

DB_PATH = 'auth.db'
db_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_connection() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT    UNIQUE NOT NULL,
                password_hash TEXT    NOT NULL,
                salt          TEXT    NOT NULL,
                created_at    REAL    NOT NULL
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS tokens (
                token      TEXT PRIMARY KEY,
                username   TEXT NOT NULL,
                expires_at REAL NOT NULL
            )
        ''')
        conn.commit()
    print('[Auth] Database initialised.')


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def hash_password(password: str, salt: str) -> str:
    """PBKDF2-HMAC-SHA256 hash for safe password storage."""
    dk = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt.encode('utf-8'),
        iterations=260_000
    )
    return dk.hex()


def verify_password(password: str, salt: str, stored_hash: str) -> bool:
    candidate = hash_password(password, salt)
    return hmac.compare_digest(candidate, stored_hash)


# ---------------------------------------------------------------------------
# RPC handler class
# ---------------------------------------------------------------------------

class AuthHandler(SimpleXMLRPCRequestHandler):
    rpc_paths = ('/RPC2',)


class AuthService:
    """Exposed as an XML-RPC service."""

    # ------------------------------------------------------------------
    # register(username, password) -> {"success": bool, "message": str}
    # ------------------------------------------------------------------
    def register(self, username: str, password: str) -> dict:
        username = username.strip()
        if not username or not password:
            return {"success": False, "message": "Username and password required."}
        if len(username) < 2 or len(username) > 32:
            return {"success": False, "message": "Username must be 2-32 characters."}
        if len(password) < 4:
            return {"success": False, "message": "Password must be at least 4 characters."}

        salt = uuid.uuid4().hex
        pw_hash = hash_password(password, salt)

        with db_lock:
            try:
                with get_connection() as conn:
                    conn.execute(
                        'INSERT INTO users (username, password_hash, salt, created_at) VALUES (?,?,?,?)',
                        (username, pw_hash, salt, time.time())
                    )
                    conn.commit()
            except sqlite3.IntegrityError:
                return {"success": False, "message": f"Username '{username}' is already taken."}

        print(f'[Auth] Registered user: {username}')
        return {"success": True, "message": "Registration successful."}

    # ------------------------------------------------------------------
    # login(username, password) -> {"success": bool, "token": str, "message": str}
    # ------------------------------------------------------------------
    def login(self, username: str, password: str) -> dict:
        with db_lock:
            with get_connection() as conn:
                row = conn.execute(
                    'SELECT password_hash, salt FROM users WHERE username = ?',
                    (username,)
                ).fetchone()

        if not row:
            return {"success": False, "token": "", "message": "Invalid username or password."}
        if not verify_password(password, row['salt'], row['password_hash']):
            return {"success": False, "token": "", "message": "Invalid username or password."}

        token = uuid.uuid4().hex
        expires_at = time.time() + config.TOKEN_EXPIRY_SECONDS

        with db_lock:
            with get_connection() as conn:
                # Remove any existing tokens for this user
                conn.execute('DELETE FROM tokens WHERE username = ?', (username,))
                conn.execute(
                    'INSERT INTO tokens (token, username, expires_at) VALUES (?,?,?)',
                    (token, username, expires_at)
                )
                conn.commit()

        print(f'[Auth] Login: {username}')
        return {"success": True, "token": token, "message": "Login successful."}

    # ------------------------------------------------------------------
    # validate_token(token) -> {"valid": bool, "username": str, "message": str}
    # ------------------------------------------------------------------
    def validate_token(self, token: str) -> dict:
        with db_lock:
            with get_connection() as conn:
                row = conn.execute(
                    'SELECT username, expires_at FROM tokens WHERE token = ?',
                    (token,)
                ).fetchone()

        if not row:
            return {"valid": False, "username": "", "message": "Token not found."}
        if time.time() > row['expires_at']:
            # Clean up expired token
            with db_lock:
                with get_connection() as conn:
                    conn.execute('DELETE FROM tokens WHERE token = ?', (token,))
                    conn.commit()
            return {"valid": False, "username": "", "message": "Token expired."}

        return {"valid": True, "username": row['username'], "message": "OK"}

    # ------------------------------------------------------------------
    # logout(token) -> {"success": bool}
    # ------------------------------------------------------------------
    def logout(self, token: str) -> dict:
        with db_lock:
            with get_connection() as conn:
                row = conn.execute(
                    'SELECT username FROM tokens WHERE token = ?', (token,)
                ).fetchone()
                if row:
                    conn.execute('DELETE FROM tokens WHERE token = ?', (token,))
                    conn.commit()
                    print(f'[Auth] Logout: {row["username"]}')
                    return {"success": True}
        return {"success": False}

    # ------------------------------------------------------------------
    # list_users() -> list of usernames  (for admin/debug)
    # ------------------------------------------------------------------
    def list_users(self) -> list:
        with db_lock:
            with get_connection() as conn:
                rows = conn.execute('SELECT username FROM users ORDER BY created_at').fetchall()
        return [r['username'] for r in rows]

    # ------------------------------------------------------------------
    # health_check() -> str
    # ------------------------------------------------------------------
    def health_check(self) -> str:
        return "Auth Service OK"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    init_db()

    server = SimpleXMLRPCServer(
        (config.AUTH_HOST, config.AUTH_PORT),
        requestHandler=AuthHandler,
        logRequests=False,
        allow_none=True
    )
    server.register_instance(AuthService())
    server.register_introspection_functions()

    print(f'{"="*50}')
    print(f'  Auth Service (XML-RPC)')
    print(f'  Listening on {config.AUTH_HOST}:{config.AUTH_PORT}')
    print(f'  Database: {DB_PATH}')
    print(f'  Press Ctrl+C to stop')
    print(f'{"="*50}')

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n[Auth] Shutting down.')


if __name__ == '__main__':
    main()
