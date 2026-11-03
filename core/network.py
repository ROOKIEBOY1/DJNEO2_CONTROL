"""Desktop route selection; does not send a datagram."""
import socket
from handshake import DRONE_IP, DRONE_PORT, validate_drone_local_ip

def detect_local_ip():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((DRONE_IP, DRONE_PORT))  # Select a route; sends no packet.
        return validate_drone_local_ip(sock.getsockname()[0])
