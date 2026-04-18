import argparse
import base64
import os
import socket
import struct
import time
from urllib.parse import urlparse


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("socket closed while receiving frame data")
        data += chunk
    return data


def _build_frame(payload: bytes, opcode: int = 0x1) -> bytes:
    fin_and_opcode = 0x80 | (opcode & 0x0F)
    payload_len = len(payload)
    mask_key = os.urandom(4)

    if payload_len <= 125:
        header = bytes([fin_and_opcode, 0x80 | payload_len])
    elif payload_len <= 65535:
        header = bytes([fin_and_opcode, 0x80 | 126]) + struct.pack("!H", payload_len)
    else:
        header = bytes([fin_and_opcode, 0x80 | 127]) + struct.pack("!Q", payload_len)

    masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return header + mask_key + masked


def _read_frame(sock: socket.socket) -> tuple[int, bytes]:
    first_two = _recv_exact(sock, 2)
    first, second = first_two[0], first_two[1]
    opcode = first & 0x0F
    masked = (second & 0x80) != 0
    payload_len = second & 0x7F

    if payload_len == 126:
        payload_len = struct.unpack("!H", _recv_exact(sock, 2))[0]
    elif payload_len == 127:
        payload_len = struct.unpack("!Q", _recv_exact(sock, 8))[0]

    mask_key = b""
    if masked:
        mask_key = _recv_exact(sock, 4)

    payload = _recv_exact(sock, payload_len)

    if masked:
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return opcode, payload


def main() -> int:
    parser = argparse.ArgumentParser(description="WebSocket echo smoke client (no external deps)")
    parser.add_argument("--url", required=True, help="ws://host[:port]/path")
    parser.add_argument("--count", type=int, required=True, help="number of echo rounds")
    parser.add_argument("--interval", type=float, required=True, help="seconds between messages")
    parser.add_argument("--timeout", type=float, required=True, help="socket timeout seconds")
    args = parser.parse_args()

    parsed = urlparse(args.url)
    if parsed.scheme != "ws":
        raise ValueError("only ws:// is supported for this test client")

    host = parsed.hostname
    if host is None:
        raise ValueError("invalid websocket host")
    port = parsed.port or 80
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query

    key = base64.b64encode(os.urandom(16)).decode("utf-8")
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    ).encode("utf-8")

    with socket.create_connection((host, port), timeout=args.timeout) as sock:
        sock.settimeout(args.timeout)
        sock.sendall(request)

        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("socket closed during websocket handshake")
            response += chunk

        status_line = response.split(b"\r\n", 1)[0].decode("utf-8", errors="ignore")
        if "101" not in status_line:
            raise ConnectionError(f"websocket handshake failed: {status_line}")
        print(f"[ws_client] handshake ok: {status_line}")

        for i in range(1, args.count + 1):
            payload = f"ping-{i}".encode("utf-8")
            send_at = time.time()
            sock.sendall(_build_frame(payload, opcode=0x1))

            opcode, echoed = _read_frame(sock)
            if opcode != 0x1:
                raise ConnectionError(f"unexpected opcode={opcode}, expected text frame")
            if echoed != payload:
                raise ConnectionError(f"echo mismatch: sent={payload!r}, recv={echoed!r}")

            rtt_ms = (time.time() - send_at) * 1000
            print(f"[ws_client] round={i} echo_ok rtt_ms={rtt_ms:.2f}")
            time.sleep(args.interval)

        sock.sendall(_build_frame(b"", opcode=0x8))
        print("[ws_client] close sent")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
