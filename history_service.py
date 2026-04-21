"""
Handles:
  - Saving chat messages to SQLite (called by Chat Service)
  - Retrieving message history per channel (called by Gateway + Chat Service)
  - Listing active channels
  - Basic statistics
"""

import sqlite3
import time
import threading
from xmlrpc.server import SimpleXMLRPCServer, SimpleXMLRPCRequestHandler

import config

DB_PATH = 'history.db'
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
            CREATE TABLE IF NOT EXISTS messages (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                channel   TEXT    NOT NULL,
                username  TEXT    NOT NULL,
                message   TEXT    NOT NULL,
                timestamp REAL    NOT NULL
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_channel ON messages(channel)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON messages(timestamp)')
        conn.commit()
    print('[History] Database initialised.')


# ---------------------------------------------------------------------------
# RPC handler class
# ---------------------------------------------------------------------------

class HistoryHandler(SimpleXMLRPCRequestHandler):
    rpc_paths = ('/RPC2',)


class HistoryService:
    """Exposed as an XML-RPC service."""

    # ------------------------------------------------------------------
    # save_message(channel, username, message) -> {"success": bool}
    # ------------------------------------------------------------------
    def save_message(self, channel: str, username: str, message: str) -> dict:
        if not channel or not username or not message:
            return {"success": False}
        with db_lock:
            with get_connection() as conn:
                conn.execute(
                    'INSERT INTO messages (channel, username, message, timestamp) VALUES (?,?,?,?)',
                    (channel, username, message, time.time())
                )
                conn.commit()
        return {"success": True}

    # ------------------------------------------------------------------
    # get_history(channel, limit) -> list of message dicts
    # Each dict: {username, message, timestamp, formatted_time}
    # ------------------------------------------------------------------
    def get_history(self, channel: str, limit: int = config.DEFAULT_HISTORY_LIMIT) -> list:
        limit = min(max(int(limit), 1), 500)  # Clamp between 1 and 500
        with db_lock:
            with get_connection() as conn:
                rows = conn.execute('''
                    SELECT username, message, timestamp
                    FROM messages
                    WHERE channel = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                ''', (channel, limit)).fetchall()

        messages = []
        for row in reversed(rows):
            ts = row['timestamp']
            fmt = time.strftime('%H:%M:%S', time.localtime(ts))
            messages.append({
                'username': row['username'],
                'message': row['message'],
                'timestamp': ts,
                'formatted_time': fmt,
                'channel': channel
            })
        return messages

    # ------------------------------------------------------------------
    # get_channels() -> list of channel name strings
    # ------------------------------------------------------------------
    def get_channels(self) -> list:
        with db_lock:
            with get_connection() as conn:
                rows = conn.execute('''
                    SELECT channel, COUNT(*) as msg_count, MAX(timestamp) as last_active
                    FROM messages
                    GROUP BY channel
                    ORDER BY last_active DESC
                ''').fetchall()
        return [
            {
                'channel': r['channel'],
                'message_count': r['msg_count'],
                'last_active': time.strftime('%Y-%m-%d %H:%M', time.localtime(r['last_active']))
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # get_stats() -> dict with total message counts etc.
    # ------------------------------------------------------------------
    def get_stats(self) -> dict:
        with db_lock:
            with get_connection() as conn:
                total = conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
                channels = conn.execute('SELECT COUNT(DISTINCT channel) FROM messages').fetchone()[0]
                users = conn.execute('SELECT COUNT(DISTINCT username) FROM messages').fetchone()[0]
        return {
            'total_messages': total,
            'total_channels': channels,
            'total_users': users
        }

    # ------------------------------------------------------------------
    # health_check() -> str
    # ------------------------------------------------------------------
    def health_check(self) -> str:
        return "History Service OK"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    init_db()

    server = SimpleXMLRPCServer(
        (config.HISTORY_HOST, config.HISTORY_PORT),
        requestHandler=HistoryHandler,
        logRequests=False,
        allow_none=True
    )
    server.register_instance(HistoryService())
    server.register_introspection_functions()

    print(f'{"="*50}')
    print(f'  History Service (XML-RPC)')
    print(f'  Listening on {config.HISTORY_HOST}:{config.HISTORY_PORT}')
    print(f'  Database: {DB_PATH}')
    print(f'  Press Ctrl+C to stop')
    print(f'{"="*50}')

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n[History] Shutting down.')


if __name__ == '__main__':
    main()
