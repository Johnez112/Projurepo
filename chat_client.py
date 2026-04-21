import socket
import threading
import sys
import json
import argparse

try:
    import urllib.request
    import urllib.error
except ImportError:
    pass

# Default gateway URL (can be overridden via --gateway argument)
DEFAULT_GATEWAY = 'http://127.0.0.1:5000'


# ---------------------------------------------------------------------------
# REST helpers  (stdlib only — no requests library needed)
# ---------------------------------------------------------------------------

def http_post(url: str, payload: dict) -> dict:
    """Simple POST with JSON body using only stdlib."""
    body = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=body,
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode('utf-8'))
        except Exception:
            return {'success': False, 'message': f'HTTP {e.code}'}
    except Exception as e:
        return {'success': False, 'message': str(e)}


def http_get(url: str) -> dict:
    """Simple GET using only stdlib."""
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        return {'error': str(e)}


# ---------------------------------------------------------------------------
# Auth flow
# ---------------------------------------------------------------------------

def auth_flow(gateway_url: str) -> tuple:
# Interactive registration / login. Returns (token, username, chat_host, chat_port) or raises SystemExit.

    print(f'\n{"="*50}')
    print('  Distributed Chat Client')
    print(f'  Gateway: {gateway_url}')
    print(f'{"="*50}\n')

    # Check gateway health
    health = http_get(f'{gateway_url}/api/health')
    if 'error' in health:
        print(f'[ERROR] Cannot reach gateway at {gateway_url}')
        print('        Is the gateway (gateway.py) running?')
        sys.exit(1)

    overall = health.get('overall', 'unknown')
    if overall != 'ok':
        print(f'[WARNING] Some services are degraded: {health}')
    else:
        print('[OK] All services are online.\n')

    while True:
        print('Options:')
        print('  1. Login')
        print('  2. Register')
        print('  3. Quit')
        choice = input('Choose [1/2/3]: ').strip()

        if choice == '3':
            sys.exit(0)

        username = input('Username: ').strip()
        password = input('Password: ').strip()

        if not username or not password:
            print('[!] Username and password cannot be empty.\n')
            continue

        if choice == '2':
            result = http_post(f'{gateway_url}/api/register', {
                'username': username, 'password': password
            })
            if not result.get('success'):
                print(f'[!] Registration failed: {result.get("message")}\n')
                continue
            print(f'[OK] Registered as {username}. Logging in...')

        # Login
        result = http_post(f'{gateway_url}/api/login', {
            'username': username, 'password': password
        })
        if not result.get('success'):
            print(f'[!] Login failed: {result.get("message")}\n')
            continue

        token = result['token']
        chat_host = result.get('chat_host', '127.0.0.1')
        chat_port = result.get('chat_port', 12345)
        print(f'[OK] Logged in as {username}. Token received.')
        return token, username, chat_host, chat_port


# ---------------------------------------------------------------------------
# TCP chat
# ---------------------------------------------------------------------------

def receive_messages(sock, stop_event: threading.Event):
    """
    Background thread: receives messages from Chat Service and prints them.
    """
    while not stop_event.is_set():
        try:
            data = sock.recv(4096)
            if not data:
                if not stop_event.is_set():
                    print('\n[Disconnected from Chat Service]')
                    stop_event.set()
                break
            message = data.decode('utf-8')
            # Overwrite the "You: " prompt, print message, reprint prompt
            sys.stdout.write(f'\r{message}')
            if not stop_event.is_set():
                sys.stdout.write('You: ')
            sys.stdout.flush()
        except (ConnectionResetError, OSError):
            if not stop_event.is_set():
                print('\n[Connection lost]')
                stop_event.set()
            break


def chat_loop(sock, gateway_url: str, token: str, stop_event: threading.Event):
    """Main send loop."""
    try:
        while not stop_event.is_set():
            try:
                message = input()
            except EOFError:
                break

            if stop_event.is_set():
                break

            cmd = message.strip().lower()
            if cmd == '/quit':
                sock.sendall(message.encode('utf-8'))
                stop_event.set()
                break

            if message.strip():
                try:
                    sock.sendall(message.encode('utf-8'))
                except OSError:
                    print('[!] Send failed — connection lost.')
                    stop_event.set()
                    break
    except KeyboardInterrupt:
        print('\n[Disconnecting...]')
        stop_event.set()
        try:
            sock.sendall('/quit'.encode('utf-8'))
        except Exception:
            pass
    finally:
        # Logout via gateway
        try:
            http_post(f'{gateway_url}/api/logout', {'token': token})
        except Exception:
            pass
        sock.close()
        print('[Disconnected from chat.]')


def connect_to_chat(token: str, chat_host: str, chat_port: int,
                    gateway_url: str):
    """Connect to the Chat Service TCP socket and run the chat loop."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((chat_host, chat_port))
        print(f'[OK] Connected to Chat Service at {chat_host}:{chat_port}')
    except ConnectionRefusedError:
        print(f'[ERROR] Cannot connect to Chat Service at {chat_host}:{chat_port}')
        print('        Is chat_service.py running?')
        sys.exit(1)

    stop_event = threading.Event()

    # Receiver thread
    recv_thread = threading.Thread(
        target=receive_messages,
        args=(sock, stop_event),
        daemon=True
    )
    recv_thread.start()

    # The Chat Service will first prompt for token, then channel
    # We need to send them; but since they're prompts (not commands),
    # the recv thread will display them and the send loop provides answers.
    # We auto-send the token immediately after connection.
    import time
    time.sleep(0.3)  # Give recv thread time to print the "Enter your token:" prompt
    try:
        sock.sendall(token.encode('utf-8'))
    except OSError:
        print('[ERROR] Connection failed immediately.')
        sys.exit(1)

    chat_loop(sock, gateway_url, token, stop_event)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Distributed Chat Client')
    parser.add_argument(
        '--gateway',
        default=DEFAULT_GATEWAY,
        help=f'API Gateway URL (default: {DEFAULT_GATEWAY})'
    )
    args = parser.parse_args()
    gateway_url = args.gateway.rstrip('/')

    # Step 1: Authenticate via REST
    token, username, chat_host, chat_port = auth_flow(gateway_url)

    # Step 2: Connect to Chat Service via TCP
    connect_to_chat(token, chat_host, chat_port, gateway_url)


if __name__ == '__main__':
    main()
