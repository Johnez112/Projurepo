"""
Extends the original gateway with:
  - Serves the web UI (index.html)
  - Bridges browser between TCP Chat Service via SSE + REST
  - Each logged-in browser tab gets its own TCP connection to Chat Service

New endpoints:
  GET  /                       Serve web UI
  POST /api/chat/connect       Open TCP conn to Chat Service + handshake
  GET  /api/chat/stream        SSE stream of incoming messages
  POST /api/chat/send          Send a message via stored TCP socket
  POST /api/chat/join          Switch channel
  POST /api/chat/disconnect    Close TCP connection
"""

import socket as tcp_socket
import threading
import queue
import time
import json
import os
import xmlrpc.client

from flask import Flask, request, jsonify, Response, render_template

import config

# Resolve paths relative to this script file, not the working directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')

app = Flask(__name__, template_folder=TEMPLATE_DIR)

# token -> {"sock", "queue", "username", "channel", "thread"}
active_connections: dict = {}
conn_lock = threading.Lock()


# ---------------------------------------------------------------------------
# RPC helpers (same as original gateway)
# ---------------------------------------------------------------------------

def auth_rpc():
    return xmlrpc.client.ServerProxy(
        f'http://{config.AUTH_HOST}:{config.AUTH_PORT}', allow_none=True)


def history_rpc():
    return xmlrpc.client.ServerProxy(
        f'http://{config.HISTORY_HOST}:{config.HISTORY_PORT}', allow_none=True)


def service_unavailable(name):
    return jsonify({'success': False,
                    'message': f'{name} unavailable. Is it running?'}), 503


def validate_token_rpc(token: str):
    try:
        r = auth_rpc().validate_token(token)
        return r['valid'], r.get('username', '')
    except Exception:
        return False, ''


# ---------------------------------------------------------------------------
# TCP reader thread
# ---------------------------------------------------------------------------

def _reader(token: str, sock, msg_queue: queue.Queue):
    """Runs in background. Reads from TCP and enqueues messages."""
    while True:
        try:
            data = sock.recv(4096)
            if not data:
                msg_queue.put(None)
                break
            msg_queue.put(data.decode('utf-8'))
        except OSError:
            msg_queue.put(None)
            break


# ---------------------------------------------------------------------------
# Original auth / history REST endpoints
# ---------------------------------------------------------------------------

@app.route('/api/register', methods=['POST'])
def register():
    data = request.get_json(silent=True) or {}
    u, p = data.get('username', '').strip(), data.get('password', '').strip()
    if not u or not p:
        return jsonify({'success': False, 'message': 'username and password required.'}), 400
    try:
        r = auth_rpc().register(u, p)
        return jsonify(r), 201 if r['success'] else 400
    except Exception:
        return service_unavailable('Auth Service')


@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json(silent=True) or {}
    u, p = data.get('username', '').strip(), data.get('password', '').strip()
    if not u or not p:
        return jsonify({'success': False, 'message': 'username and password required.'}), 400
    try:
        r = auth_rpc().login(u, p)
        if r['success']:
            r['username'] = u          # add username so JS can display it
            r['chat_host'] = '127.0.0.1'
            r['chat_port'] = config.CHAT_PORT
        return jsonify(r), 200 if r['success'] else 401
    except Exception:
        return service_unavailable('Auth Service')


@app.route('/api/logout', methods=['POST'])
def logout():
    data = request.get_json(silent=True) or {}
    token = data.get('token', '').strip()
    _close_connection(token)
    try:
        r = auth_rpc().logout(token)
        return jsonify(r)
    except Exception:
        return service_unavailable('Auth Service')


@app.route('/api/history/<channel>')
def get_history(channel):
    limit = request.args.get('limit', config.DEFAULT_HISTORY_LIMIT, type=int)
    try:
        msgs = history_rpc().get_history(channel, limit)
        return jsonify({'channel': channel, 'messages': msgs, 'count': len(msgs)})
    except Exception:
        return service_unavailable('History Service')


@app.route('/api/channels')
def get_channels():
    try:
        return jsonify({'channels': history_rpc().get_channels()})
    except Exception:
        return service_unavailable('History Service')


@app.route('/api/stats')
def get_stats():
    try:
        return jsonify(history_rpc().get_stats())
    except Exception:
        return service_unavailable('History Service')


@app.route('/api/health')
def health():
    status = {}
    for name, fn in [('auth_service', lambda: auth_rpc().health_check()),
                     ('history_service', lambda: history_rpc().health_check())]:
        try:
            status[name] = {'status': 'ok', 'message': fn()}
        except Exception as e:
            status[name] = {'status': 'error', 'message': str(e)}
    status['gateway'] = {'status': 'ok', 'message': 'Gateway OK'}
    ok = all(v['status'] == 'ok' for v in status.values())
    return jsonify({'services': status, 'overall': 'ok' if ok else 'degraded'}), 200 if ok else 503


# ---------------------------------------------------------------------------
# Chat bridge  (SSE + REST → TCP)
# ---------------------------------------------------------------------------

def _close_connection(token: str):
    with conn_lock:
        conn = active_connections.pop(token, None)
    if conn:
        try:
            conn['sock'].sendall('/quit'.encode('utf-8'))
        except Exception:
            pass
        try:
            conn['sock'].close()
        except Exception:
            pass


@app.route('/api/chat/connect', methods=['POST'])
def chat_connect():
    """Open a TCP connection to Chat Service on behalf of the browser client."""
    data = request.get_json(silent=True) or {}
    token = data.get('token', '').strip()
    channel = data.get('channel', 'general').strip() or 'general'

    print(f'[Gateway] /api/chat/connect — token={token[:8]}... channel={channel}')

    if not token:
        print('[Gateway] connect rejected: no token')
        return jsonify({'success': False, 'message': 'Token puuttuu.'}), 400

    valid, username = validate_token_rpc(token)
    print(f'[Gateway] token valid={valid} username={username}')
    if not valid:
        return jsonify({'success': False, 'message': 'Invalid or expired token.'}), 401

    # Close any existing connection for this token
    _close_connection(token)

    # Connect to Chat Service
    chat_host = '127.0.0.1'  # connect address (CHAT_HOST is bind address)
    sock = tcp_socket.socket(tcp_socket.AF_INET, tcp_socket.SOCK_STREAM)
    sock.settimeout(6)
    try:
        sock.connect((chat_host, config.CHAT_PORT))
    except Exception as e:
        return jsonify({'success': False, 'message': f'Cannot reach Chat Service: {e}'}), 503

    # Handshake: send token + channel, read welcome + history
    initial_messages = []
    try:
        prompt1 = sock.recv(256).decode('utf-8')   # token prompt
        sock.sendall(token.encode('utf-8'))
        time.sleep(0.4)
        prompt2 = sock.recv(4096).decode('utf-8')   # welcome + channel prompt
        if 'ERROR' in prompt2:
            sock.close()
            return jsonify({'success': False, 'message': prompt2.strip()}), 401
        sock.sendall(channel.encode('utf-8'))
        time.sleep(0.5)
        # Read join confirmation + history dump
        sock.settimeout(1.5)
        try:
            while True:
                chunk = sock.recv(4096).decode('utf-8')
                if not chunk:
                    break
                initial_messages.append(chunk)
        except tcp_socket.timeout:
            pass
    except Exception as e:
        sock.close()
        return jsonify({'success': False, 'message': f'Handshake failed: {e}'}), 503

    sock.settimeout(None)

    msg_queue: queue.Queue = queue.Queue()
    t = threading.Thread(target=_reader, args=(token, sock, msg_queue), daemon=True)
    t.start()

    with conn_lock:
        active_connections[token] = {
            'sock': sock,
            'queue': msg_queue,
            'username': username,
            'channel': channel,
            'thread': t,
        }

    return jsonify({
        'success': True,
        'username': username,
        'channel': channel,
        'initial': ''.join(initial_messages),
    })

@app.route('/api/chat/stream')
def chat_stream():
    """SSE endpoint — streams messages from the TCP connection."""
    token = request.args.get('token', '')

    def generate():
        while True:
            with conn_lock:
                conn = active_connections.get(token)
            if not conn:
                yield f'data: {json.dumps({"error": "not_connected"})}\n\n'
                return
            try:
                msg = conn['queue'].get(timeout=20)
                if msg is None:
                    yield f'data: {json.dumps({"disconnected": True})}\n\n'
                    return
                yield f'data: {json.dumps({"message": msg})}\n\n'
            except queue.Empty:
                yield ': heartbeat\n\n'   # keeps connection alive

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        }
    )

@app.route('/api/chat/send', methods=['POST'])
def chat_send():
    """Send a message through the stored TCP connection."""
    data = request.get_json(silent=True) or {}
    token = data.get('token', '')
    message = data.get('message', '').strip()
    if not message:
        return jsonify({'success': False, 'message': 'Empty message.'}), 400

    with conn_lock:
        conn = active_connections.get(token)
    if not conn:
        return jsonify({'success': False, 'message': 'Not connected.'}), 400
    try:
        conn['sock'].sendall(message.encode('utf-8'))
        return jsonify({'success': True})
    except OSError as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/chat/join', methods=['POST'])
def chat_join():
    """Send a /join <channel> command."""
    data = request.get_json(silent=True) or {}
    token = data.get('token', '')
    channel = data.get('channel', '').strip()
    if not channel:
        return jsonify({'success': False}), 400
    with conn_lock:
        conn = active_connections.get(token)
    if not conn:
        return jsonify({'success': False, 'message': 'Not connected.'}), 400
    try:
        conn['sock'].sendall(f'/join {channel}'.encode('utf-8'))
        conn['channel'] = channel
        return jsonify({'success': True})
    except OSError as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/chat/users')
def chat_users():
    """Return list of usernames currently online in the given channel.
    Looks at active_connections which the gateway itself maintains."""
    token_param = request.args.get('token', '')
    channel = request.args.get('channel', '')

    # Validate token
    valid, _ = validate_token_rpc(token_param)
    if not valid:
        return jsonify({'users': []}), 401

    users = []
    with conn_lock:
        for tok, conn in active_connections.items():
            if not channel or conn.get('channel') == channel:
                users.append(conn['username'])

    return jsonify({'users': sorted(users)})

@app.route('/api/chat/update_channel', methods=['POST'])
def chat_update_channel():
    """Called by JS when CHANNEL_CHANGED: is received via SSE (user typed /join).
    Updates gateway's internal channel tracking so loadUsers() stays accurate."""
    data = request.get_json(silent=True) or {}
    token = data.get('token', '')
    channel = data.get('channel', '').strip()
    if not token or not channel:
        return jsonify({'success': False}), 400
    with conn_lock:
        conn = active_connections.get(token)
        if conn:
            conn['channel'] = channel
    return jsonify({'success': True})

@app.route('/api/chat/disconnect', methods=['POST'])
def chat_disconnect():
    data = request.get_json(silent=True) or {}
    token = data.get('token', '')
    _close_connection(token)
    return jsonify({'success': True})

# ---------------------------------------------------------------------------
# Web UI — served directly from Flask
# ---------------------------------------------------------------------------

@app.route('/') 
def index():
    return render_template('index.html')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    print('=' * 50)
    print('  Web Gateway (Flask + SSE bridge)')
    print(f'  Open http://127.0.0.1:{config.GATEWAY_PORT} in your browser')
    print(f'  Auth RPC    → {config.AUTH_HOST}:{config.AUTH_PORT}')
    print(f'  History RPC → {config.HISTORY_HOST}:{config.HISTORY_PORT}')
    print(f'  Chat TCP    → 127.0.0.1:{config.CHAT_PORT}')
    print('  Press Ctrl+C to stop')
    print('=' * 50)
    app.run(
        host=config.GATEWAY_HOST,
        port=config.GATEWAY_PORT,
        debug=False,
        threaded=True,
    )
