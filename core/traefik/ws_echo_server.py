import asyncio
import base64
import hashlib
import os
import struct
from typing import Optional


MAGIC_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _to_websocket_accept(sec_ws_key: str) -> str:
    digest = hashlib.sha1((sec_ws_key + MAGIC_WS_GUID).encode("utf-8")).digest()
    return base64.b64encode(digest).decode("utf-8")


def _build_frame(payload: bytes, opcode: int = 0x1) -> bytes:
    fin_and_opcode = 0x80 | (opcode & 0x0F)
    payload_len = len(payload)
    if payload_len <= 125:
        header = bytes([fin_and_opcode, payload_len])
    elif payload_len <= 65535:
        header = bytes([fin_and_opcode, 126]) + struct.pack("!H", payload_len)
    else:
        header = bytes([fin_and_opcode, 127]) + struct.pack("!Q", payload_len)
    return header + payload


async def _read_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    first_two = await reader.readexactly(2)
    first, second = first_two[0], first_two[1]
    opcode = first & 0x0F
    masked = (second & 0x80) != 0
    payload_len = second & 0x7F

    if payload_len == 126:
        payload_len = struct.unpack("!H", await reader.readexactly(2))[0]
    elif payload_len == 127:
        payload_len = struct.unpack("!Q", await reader.readexactly(8))[0]

    mask_key: Optional[bytes] = None
    if masked:
        mask_key = await reader.readexactly(4)

    payload = await reader.readexactly(payload_len)
    if masked and mask_key:
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return opcode, payload


async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    try:
        request_data = await reader.readuntil(b"\r\n\r\n")
    except asyncio.IncompleteReadError:
        writer.close()
        await writer.wait_closed()
        return

    request_text = request_data.decode("utf-8", errors="ignore")
    lines = [line for line in request_text.split("\r\n") if line]
    if not lines:
        writer.close()
        await writer.wait_closed()
        return

    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()

    sec_ws_key = headers.get("sec-websocket-key")
    upgrade = headers.get("upgrade", "").lower()
    connection = headers.get("connection", "").lower()

    if not sec_ws_key or upgrade != "websocket" or "upgrade" not in connection:
        writer.write(
            b"HTTP/1.1 400 Bad Request\r\n"
            b"Content-Type: text/plain\r\n"
            b"Content-Length: 29\r\n\r\n"
            b"websocket upgrade required\n"
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        return

    accept_value = _to_websocket_accept(sec_ws_key)
    writer.write(
        (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept_value}\r\n\r\n"
        ).encode("utf-8")
    )
    await writer.drain()

    print(f"[ws_echo] connected: {peer}", flush=True)

    try:
        while True:
            opcode, payload = await _read_frame(reader)

            if opcode == 0x8:
                writer.write(_build_frame(payload, opcode=0x8))
                await writer.drain()
                break
            if opcode == 0x9:
                writer.write(_build_frame(payload, opcode=0xA))
                await writer.drain()
                continue
            if opcode in (0x1, 0x2):
                writer.write(_build_frame(payload, opcode=opcode))
                await writer.drain()
                continue
    except (asyncio.IncompleteReadError, ConnectionResetError):
        pass
    finally:
        print(f"[ws_echo] disconnected: {peer}", flush=True)
        writer.close()
        await writer.wait_closed()


async def _main() -> None:
    host = os.environ.get("WS_ECHO_HOST", "0.0.0.0")
    port = int(os.environ.get("WS_ECHO_PORT", "9000"))
    server = await asyncio.start_server(_handle_client, host=host, port=port)
    print(f"[ws_echo] listening on {host}:{port}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(_main())
