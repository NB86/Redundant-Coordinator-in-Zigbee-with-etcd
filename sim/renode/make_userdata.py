"""
Generate the flash user-data page image loaded into both simulated NCPs.

Erased (0xFF) like an unprogrammed part, so every manufacturing token takes its default, except
the MFG_ASH_CONFIG "rebootDelay" entry (token at 0x038, uint16 index 10 -> offset 0x04C) set to
0 ms. The default 1000 ms delay before RSTACK exists for slow RS-232 converters; over a TCP socket
it only pushes the simulated reboot past bellows' 2.5 s reset timeout.
"""
import sys

out = sys.argv[1] if len(sys.argv) > 1 else "userdata_sim.bin"
img = bytearray(b"\xff" * 0x800)
img[0x4C:0x4E] = (0).to_bytes(2, "little")
with open(out, "wb") as f:
    f.write(img)
print(f"wrote {out} ({len(img)} bytes)")
