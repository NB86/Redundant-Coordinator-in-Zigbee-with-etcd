#!/usr/bin/env python3
"""Minimal ASH (EZSP-UART) handshake test against a Renode socket terminal.

Sequence: connect -> collect boot bytes -> send RST -> expect RSTACK ->
send EZSP 'version' DATA frame -> expect a DATA frame back.
Exit code 0 only if a DATA frame is received from the NCP.
"""
import socket, sys, time

HOST = "127.0.0.1"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 24843
TIMEOUT = float(sys.argv[2]) if len(sys.argv) > 2 else 25.0
STEP = float(sys.argv[3]) if len(sys.argv) > 3 else 10.0

RESERVED = {0x7E, 0x7D, 0x11, 0x13, 0x18, 0x1A}

def crc16(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc

def randomize(data):
    out, r = bytearray(), 0x42
    for b in data:
        out.append(b ^ r)
        r = (r >> 1) ^ (0xB8 if r & 1 else 0)
    return bytes(out)

def stuff(data):
    out = bytearray()
    for b in data:
        if b in RESERVED:
            out += bytes([0x7D, b ^ 0x20])
        else:
            out.append(b)
    return bytes(out)

def unstuff(data):
    out, esc = bytearray(), False
    for b in data:
        if esc:
            out.append(b ^ 0x20); esc = False
        elif b == 0x7D:
            esc = True
        else:
            out.append(b)
    return bytes(out)

def data_frame(frm, ack, payload):
    ctrl = (frm << 4) | ack
    body = bytes([ctrl]) + randomize(payload)
    body += crc16(body).to_bytes(2, "big")
    return stuff(body) + b"\x7E"

def hexs(b):
    return " ".join(f"{x:02X}" for x in b)

def read_frames(sock, deadline, want=None):
    """Collect bytes until a frame matching `want(frame)` arrives or deadline passes."""
    buf, frames = bytearray(), []
    while time.time() < deadline:
        sock.settimeout(max(0.05, min(0.5, deadline - time.time())))
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            continue
        if not chunk:
            print("!! socket closed by peer"); break
        buf += chunk
        while 0x7E in buf:
            i = buf.index(0x7E)
            raw = bytes(buf[:i]); del buf[:i + 1]
            raw = raw.lstrip(b"\x1A\x11\x13")  # strip CANCEL / XON / XOFF
            if not raw:
                continue
            frames.append(raw)
            describe(raw)
            if want and want(raw):
                return frames, True
    if buf:
        print(f"   (unterminated bytes: {hexs(buf)})")
    return frames, False

def describe(raw):
    f = unstuff(raw)
    ctrl = f[0]
    if ctrl == 0xC1:
        print(f"<< RSTACK   {hexs(f)}  (version={f[1]}, resetCode=0x{f[2]:02X})")
    elif ctrl == 0xC2:
        print(f"<< ERROR    {hexs(f)}  (version={f[1]}, errorCode=0x{f[2]:02X})")
    elif ctrl & 0x80 == 0:
        body, crc = f[:-2], int.from_bytes(f[-2:], "big")
        ok = crc16(body) == crc
        ezsp = randomize(body[1:])
        print(f"<< DATA     frm={ctrl >> 4} ack={ctrl & 7} re={(ctrl >> 3) & 1} crc={'ok' if ok else 'BAD'} ezsp=[{hexs(ezsp)}]")
    elif ctrl & 0xE0 == 0x80:
        print(f"<< ACK      ack={ctrl & 7} nRdy={(ctrl >> 3) & 1}")
    elif ctrl & 0xE0 == 0xA0:
        print(f"<< NAK      ack={ctrl & 7} nRdy={(ctrl >> 3) & 1}")
    else:
        print(f"<< ??       {hexs(f)}")

def is_data(raw):
    return unstuff(raw)[0] & 0x80 == 0

def main():
    end = time.time() + TIMEOUT
    s = socket.create_connection((HOST, PORT), timeout=5)
    print(f"connected to {HOST}:{PORT}")
    print("-- listening for spontaneous boot output (3s)")
    read_frames(s, time.time() + 3)
    rst = b"\x1A\xC0\x38\xBC\x7E"
    print(f">> RST      {hexs(rst)}")
    s.sendall(rst)
    _, got = read_frames(s, min(end, time.time() + STEP), want=lambda r: unstuff(r)[0] == 0xC1)
    if not got:
        print("RESULT: FAIL - no RSTACK after RST"); return 2
    # EZSP legacy 'version' command: seq=0, frameControl=0, frameId=0x00, desiredProtocolVersion=8
    payload = bytes([0x00, 0x00, 0x00, 0x08])
    frame = data_frame(0, 0, payload)
    print(f">> DATA     frm=0 ack=0 ezsp=[{hexs(payload)}]  raw={hexs(frame)}")
    s.sendall(frame)
    _, got = read_frames(s, min(end, time.time() + STEP), want=is_data)
    if not got:
        print("RESULT: FAIL - RSTACK received but no DATA frame reply to EZSP version"); return 3
    ack = bytes([0x80 | 1])
    ack += crc16(ack).to_bytes(2, "big") + b"\x7E"
    s.sendall(ack)
    print(">> ACK      ack=1")
    print("RESULT: PASS - NCP answered the EZSP version command over ASH")
    return 0

if __name__ == "__main__":
    sys.exit(main())
