import subprocess
import time
import socket
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICES_DIR = os.path.join(BASE_DIR, "services")

def wait_for_port(port):
    while True:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=1)
            s.close()
            break
        except:
            time.sleep(1)

print("Starting Auth Service...")
subprocess.Popen(["python", "auth_service.py"], cwd=SERVICES_DIR)
wait_for_port(8001)

print("Starting History Service...")
subprocess.Popen(["python", "history_service.py"], cwd=SERVICES_DIR)
wait_for_port(8002)

print("Starting Chat Service...")
subprocess.Popen(["python", "chat_service.py"], cwd=SERVICES_DIR)
wait_for_port(12345)

print("Starting Web Gateway...")
subprocess.Popen(["python", "web_gateway.py"], cwd=SERVICES_DIR)

print("All services started.")