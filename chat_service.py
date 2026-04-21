import socket
import threading
import xmlrpc.client
import time
import sys

import config

HOST = config.CHAT_HOST
PORT = config.CHAT_PORT

clients = {}    # sock -> {"username": str, "channel": str, "token": str}
channels = {}   # channel_name -> set of sockets
lock = threading.Lock()


# ---------------------------------------------------------------------------
# RPC clients  
# ---------------------------------------------------------------------------

def get_auth_rpc():
    return xmlrpc.client.ServerProxy(
        f'http://{config.AUTH_HOST}:{config.AUTH_PORT}',
        allow_none=True
    )


def get_history_rpc():
    return xmlrpc.client.ServerProxy(
        f'http://{config.HISTORY_HOST}:{config.HISTORY_PORT}',
        allow_none=True
    )


def validate_token(token: str):
    """Returns (valid: bool, username: str)."""
    try:
        result = get_auth_rpc().validate_token(token)
        return result['valid'], result.get('username', '')
    except Exception as e:
        print(f'[Chat] Auth RPC error: {e}')
        return False, ''


def save_message(channel: str, username: str, message: str):
    """Fire-and-forget save to history service."""
    try:
        get_history_rpc().save_message(channel, username, message)
    except Exception as e:
        print(f'[Chat] History RPC error (save): {e}')


def fetch_history(channel: str, limit: int = 20) -> list:
    """Returns list of message dicts from history service."""
    try:
        return get_history_rpc().get_history(channel, limit)
    except Exception as e:
        print(f'[Chat] History RPC error (fetch): {e}')
        return []


# ---------------------------------------------------------------------------
# Networking helpers
# ---------------------------------------------------------------------------

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind((HOST, PORT))
server.listen(10)


def send_to_client(sock, message: str):
    """Safely send UTF-8 message to one client."""
    try:
        sock.sendall(message.encode('utf-8'))
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass


def broadcast(message: str, channel: str, exclude_sock=None):
    """Send message to all clients in a channel."""
    with lock:
        members = channels.get(channel, set()).copy()
    for s in members:
        if s != exclude_sock:
            send_to_client(s, message)


def list_channels() -> str:
    with lock:
        if not channels:
            return 'No active channels.\n'
        lines = ['Active channels:']
        for name, members in channels.items():
            lines.append(f'  #{name} ({len(members)} users)')
        return '\n'.join(lines) + '\n'


def list_users(channel: str) -> str:
    with lock:
        members = channels.get(channel, set())
        if not members:
            return f'No users in #{channel}.\n'
        names = [clients[s]['username'] for s in members if s in clients]
    return f'Users in #{channel}: {", ".join(names)}\n'


def remove_client(sock):
    """Remove client from all data structures and notify channel."""
    with lock:
        if sock not in clients:
            return
        info = clients.pop(sock)
        username = info['username']
        channel = info['channel']
        if channel in channels:
            channels[channel].discard(sock)
            if not channels[channel]:
                del channels[channel]
    broadcast(f'*** {username} has left #{channel} ***\n', channel)
    try:
        sock.close()
    except OSError:
        pass
    print(f'[Chat] {username} disconnected from #{channel}')


# ---------------------------------------------------------------------------
# Client handler
# ---------------------------------------------------------------------------

def handle_client(sock, addr):
    """
    Protocol:
      1. Ask for token  (obtained from Gateway before connecting)
      2. Validate token via Auth Service RPC
      3. Ask for initial channel
      4. Deliver last 20 messages from that channel
      5. Command loop
    """
    try:
        # --- Step 1 & 2: Authentication ---
        send_to_client(sock, 'Enter your session token: ')
        token_data = sock.recv(1024)
        if not token_data:
            sock.close()
            return
        token = token_data.decode('utf-8').strip()

        valid, username = validate_token(token)
        if not valid:
            send_to_client(sock, 'ERROR: Invalid or expired token. Please login via the gateway.\n')
            sock.close()
            print(f'[Chat] Rejected unauthenticated connection from {addr}')
            return

        # Check if this user is already connected
        with lock:
            for s, info in clients.items():
                if info['username'] == username:
                    send_to_client(sock, f'ERROR: {username} is already connected.\n')
                    sock.close()
                    return

        send_to_client(sock, f'Welcome, {username}!\n')

        # --- Step 3: Channel ---
        send_to_client(sock, 'Enter channel to join (default: general): ')
        ch_data = sock.recv(1024)
        channel = ch_data.decode('utf-8').strip() if ch_data else 'general'
        if not channel:
            channel = 'general'

        # --- Register client ---
        with lock:
            clients[sock] = {'username': username, 'channel': channel, 'token': token}
            if channel not in channels:
                channels[channel] = set()
            channels[channel].add(sock)

        print(f'[Chat] {username} ({addr[0]}:{addr[1]}) joined #{channel}')
        broadcast(f'*** {username} has joined #{channel} ***\n', channel, exclude_sock=sock)
        send_to_client(sock, f'Joined #{channel}. Type /help for commands.\n')

        # --- Step 4: Deliver history ---
        history = fetch_history(channel, limit=20)
        if history:
            send_to_client(sock, f'--- Last {len(history)} messages in #{channel} ---\n')
            for msg in history:
                send_to_client(sock, f'[{msg["formatted_time"]}] {msg["username"]}: {msg["message"]}\n')
            send_to_client(sock, '--- End of history ---\n')

        # --- Step 5: Command loop ---
        while True:
            data = sock.recv(4096)
            if not data:
                break

            message = data.decode('utf-8').strip()
            if not message:
                continue

            # /quit
            if message.lower() == '/quit':
                send_to_client(sock, 'Goodbye!\n')
                break

            # /help
            elif message.lower() == '/help':
                send_to_client(sock, (
                    'Commands:\n'
                    '  /pm <user> <msg>    Private message\n'
                    '  /join <channel>     Switch channel\n'
                    '  /channels           List channels\n'
                    '  /users              List users here\n'
                    '  /history [n]        Show last n messages (default 20)\n'

                    '  /quit               Disconnect\n'
                ))

            # /pm
            elif message.startswith('/pm '):
                parts = message.split(' ', 2)
                if len(parts) < 3:
                    send_to_client(sock, 'Usage: /pm <username> <message>\n')
                    continue
                target_name, pm_text = parts[1], parts[2]
                target_sock = None
                with lock:
                    for s, info in clients.items():
                        if info['username'] == target_name:
                            target_sock = s
                            break
                if target_sock:
                    send_to_client(target_sock, f'[PM from {username}]: {pm_text}\n')
                    send_to_client(sock, f'[PM to {target_name}]: {pm_text}\n')
                else:
                    send_to_client(sock, f"User '{target_name}' not found.\n")

            # /join
            elif message.startswith('/join '):
                new_channel = message.split(' ', 1)[1].strip()
                if not new_channel:
                    send_to_client(sock, 'Usage: /join <channel>\n')
                    continue
                with lock:
                    old_channel = clients[sock]['channel']
                    if old_channel == new_channel:
                        send_to_client(sock, f'Already in #{new_channel}.\n')
                        continue
                    channels[old_channel].discard(sock)
                    if not channels[old_channel]:
                        del channels[old_channel]
                    clients[sock]['channel'] = new_channel
                    if new_channel not in channels:
                        channels[new_channel] = set()
                    channels[new_channel].add(sock)

                broadcast(f'*** {username} has left #{old_channel} ***\n', old_channel)
                broadcast(f'*** {username} has joined #{new_channel} ***\n', new_channel, exclude_sock=sock)
                send_to_client(sock, f'CHANNEL_CHANGED:{new_channel}\n')
                channel = new_channel
                print(f'[Chat] {username} moved #{old_channel} → #{new_channel}')

                # Deliver history for new channel
                history = fetch_history(new_channel, limit=10)
                if history:
                    send_to_client(sock, f'--- Last {len(history)} messages in #{new_channel} ---\n')
                    for msg in history:
                        send_to_client(sock, f'[{msg["formatted_time"]}] {msg["username"]}: {msg["message"]}\n')
                    send_to_client(sock, '--- End of history ---\n')

            # /channels
            elif message.lower() == '/channels':
                send_to_client(sock, list_channels())

            # /users
            elif message.lower() == '/users':
                with lock:
                    ch = clients[sock]['channel']
                send_to_client(sock, list_users(ch))

            # /history [n]
            elif message.lower().startswith('/history'):
                parts = message.split()
                limit = 20
                if len(parts) > 1:
                    try:
                        limit = int(parts[1])
                    except ValueError:
                        send_to_client(sock, 'Usage: /history [number]\n')
                        continue
                with lock:
                    ch = clients[sock]['channel']
                history = fetch_history(ch, limit=limit)
                if history:
                    send_to_client(sock, f'--- Last {len(history)} messages in #{ch} ---\n')
                    for msg in history:
                        send_to_client(sock, f'[{msg["formatted_time"]}] {msg["username"]}: {msg["message"]}\n')
                    send_to_client(sock, '--- End of history ---\n')
                else:
                    send_to_client(sock, f'No history for #{ch}.\n')

            # Regular message → broadcast + save
            else:
                with lock:
                    ch = clients[sock]['channel']
                timestamp = time.strftime('%H:%M:%S')
                broadcast(f'[{timestamp}] [{ch}] {username}: {message}\n', ch, exclude_sock=sock)
                send_to_client(sock, f'[{timestamp}] You: {message}\n')
                # Save to History Service asynchronously
                t = threading.Thread(
                    target=save_message,
                    args=(ch, username, message),
                    daemon=True
                )
                t.start()

    except (ConnectionResetError, BrokenPipeError, OSError) as e:
        print(f'[Chat] Connection error {addr}: {e}')
    except Exception as e:
        print(f'[Chat] Unexpected error {addr}: {e}')
    finally:
        remove_client(sock)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f'{"="*50}')
    print(f'  Chat Service (TCP Sockets)')
    print(f'  Listening on {HOST}:{PORT}')
    print(f'  Auth RPC  → {config.AUTH_HOST}:{config.AUTH_PORT}')
    print(f'  History RPC → {config.HISTORY_HOST}:{config.HISTORY_PORT}')
    print(f'  Press Ctrl+C to stop')
    print(f'{"="*50}')

    try:
        while True:
            client_sock, addr = server.accept()
            print(f'[Chat] New connection from {addr[0]}:{addr[1]}')
            thread = threading.Thread(
                target=handle_client,
                args=(client_sock, addr),
                daemon=True
            )
            thread.start()
    except KeyboardInterrupt:
        print('\n[Chat] Shutting down.')
    finally:
        server.close()


if __name__ == '__main__':
    main()
