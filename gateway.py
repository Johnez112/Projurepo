"""
Single public-facing entry point. Proxies requests to Auth and History
services via XML-RPC. Chat Service (TCP) is accessed directly by clients
after they obtain a token here.

Endpoints:
  POST /api/register          Register a new user
  POST /api/login             Login, receive session token
  POST /api/logout            Invalidate token
  GET  /api/history/<channel> Get message history for a channel
  GET  /api/channels          List all channels with message counts
  GET  /api/stats             System-wide statistics
  GET  /api/health            Health check for all services
"""

import xmlrpc.client
from flask import Flask, request, jsonify

import config

app = Flask(__name__)


# ---------------------------------------------------------------------------
# RPC client helpers
# ---------------------------------------------------------------------------

def auth_rpc():
    return xmlrpc.client.ServerProxy(
        f'http://{config.AUTH_HOST}:{config.AUTH_PORT}',
        allow_none=True
    )


def history_rpc():
    return xmlrpc.client.ServerProxy(
        f'http://{config.HISTORY_HOST}:{config.HISTORY_PORT}',
        allow_none=True
    )


def service_unavailable(service_name: str):
    return jsonify({
        'success': False,
        'message': f'{service_name} is unavailable. Is it running?'
    }), 503


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

@app.route('/api/register', methods=['POST'])
def register():
    """
    POST /api/register
    Body: {"username": "...", "password": "..."}
    Returns: {"success": bool, "message": str}
    """
    data = request.get_json(silent=True) or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    if not username or not password:
        return jsonify({'success': False, 'message': 'username and password required.'}), 400

    try:
        result = auth_rpc().register(username, password)
        status = 201 if result['success'] else 400
        return jsonify(result), status
    except Exception:
        return service_unavailable('Auth Service')


@app.route('/api/login', methods=['POST'])
def login():
    """
    POST /api/login
    Body: {"username": "...", "password": "..."}
    Returns: {"success": bool, "token": str, "message": str,
              "chat_host": str, "chat_port": int}
    """
    data = request.get_json(silent=True) or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    if not username or not password:
        return jsonify({'success': False, 'message': 'username and password required.'}), 400

    try:
        result = auth_rpc().login(username, password)
        if result['success']:
            # Also tell the client how to reach the Chat Service
            result['chat_host'] = '127.0.0.1'
            result['chat_port'] = config.CHAT_PORT
        status = 200 if result['success'] else 401
        return jsonify(result), status
    except Exception:
        return service_unavailable('Auth Service')


@app.route('/api/logout', methods=['POST'])
def logout():
    """
    POST /api/logout
    Body: {"token": "..."}
    Returns: {"success": bool}
    """
    data = request.get_json(silent=True) or {}
    token = data.get('token', '').strip()
    if not token:
        return jsonify({'success': False, 'message': 'token required.'}), 400

    try:
        result = auth_rpc().logout(token)
        return jsonify(result), 200
    except Exception:
        return service_unavailable('Auth Service')


# ---------------------------------------------------------------------------
# History endpoints
# ---------------------------------------------------------------------------

@app.route('/api/history/<channel>', methods=['GET'])
def get_history(channel):
    """
    GET /api/history/<channel>?limit=50
    Returns: {"channel": str, "messages": [...], "count": int}
    """
    limit = request.args.get('limit', config.DEFAULT_HISTORY_LIMIT, type=int)
    try:
        messages = history_rpc().get_history(channel, limit)
        return jsonify({
            'channel': channel,
            'messages': messages,
            'count': len(messages)
        }), 200
    except Exception:
        return service_unavailable('History Service')


@app.route('/api/channels', methods=['GET'])
def get_channels():
    """
    GET /api/channels
    Returns: {"channels": [...]}
    """
    try:
        channels = history_rpc().get_channels()
        return jsonify({'channels': channels}), 200
    except Exception:
        return service_unavailable('History Service')


@app.route('/api/stats', methods=['GET'])
def get_stats():
    """
    GET /api/stats
    Returns aggregate statistics from History Service.
    """
    try:
        stats = history_rpc().get_stats()
        return jsonify(stats), 200
    except Exception:
        return service_unavailable('History Service')


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.route('/api/health', methods=['GET'])
def health_check():
    """
    GET /api/health
    Pings all backend services and reports their status.
    """
    status = {}

    try:
        msg = auth_rpc().health_check()
        status['auth_service'] = {'status': 'ok', 'message': msg}
    except Exception as e:
        status['auth_service'] = {'status': 'error', 'message': str(e)}

    try:
        msg = history_rpc().health_check()
        status['history_service'] = {'status': 'ok', 'message': msg}
    except Exception as e:
        status['history_service'] = {'status': 'error', 'message': str(e)}

    status['gateway'] = {'status': 'ok', 'message': 'Gateway OK'}

    all_ok = all(v['status'] == 'ok' for v in status.values())
    http_status = 200 if all_ok else 503
    return jsonify({'services': status, 'overall': 'ok' if all_ok else 'degraded'}), http_status


# ---------------------------------------------------------------------------
# Root 
# ---------------------------------------------------------------------------

@app.route('/', methods=['GET'])
def root():
    return jsonify({
        'name': 'Distributed Chat Gateway',
        'version': '1.0',
        'endpoints': [
            'POST /api/register',
            'POST /api/login',
            'POST /api/logout',
            'GET  /api/history/<channel>',
            'GET  /api/channels',
            'GET  /api/stats',
            'GET  /api/health',
        ]
    }), 200


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    print(f'{"="*50}')
    print(f'  API Gateway (Flask REST)')
    print(f'  Listening on http://0.0.0.0:{config.GATEWAY_PORT}')
    print(f'  Auth RPC    → {config.AUTH_HOST}:{config.AUTH_PORT}')
    print(f'  History RPC → {config.HISTORY_HOST}:{config.HISTORY_PORT}')
    print(f'  Press Ctrl+C to stop')
    print(f'{"="*50}')
    app.run(host=config.GATEWAY_HOST, port=config.GATEWAY_PORT, debug=False)
