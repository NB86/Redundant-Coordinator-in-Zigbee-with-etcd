#!/bin/bash
# One-time setup of the simulation side inside WSL. Run from the repository root
# (setup.bat does: wsl -d Ubuntu --cd <repo> -- bash sim/setup_wsl.sh).
#
#   * installs Renode (Linux portable build) into $RENODE_DIR unless one is already there,
#   * installs the platform description variant with the user-data page as plain memory,
#   * generates the user-data page image.
#
# Environment (optional):
#   RENODE_DIR   where Renode lives / gets installed   (default: ~/renode_portable)
#   RENODE_URL   portable tarball to download          (default: official latest build)
set -euo pipefail

RENODE_DIR="${RENODE_DIR:-$HOME/renode_portable}"
RENODE_URL="${RENODE_URL:-https://builds.renode.io/renode-latest.linux-portable.tar.gz}"
REPO="$(pwd)"
[ -f "$REPO/sim/renode/twonode.resc" ] || { echo "run this from the repository root"; exit 1; }

for tool in python3 tar; do
  command -v "$tool" >/dev/null || { echo "missing '$tool' in WSL (sudo apt install $tool)"; exit 1; }
done

# --- 1. Renode ----------------------------------------------------------------------------------
if [ ! -x "$RENODE_DIR/renode" ]; then
  echo "Renode not found in $RENODE_DIR, downloading $RENODE_URL ..."
  tmp="$(mktemp -d)"
  if command -v curl >/dev/null; then curl -fL --progress-bar -o "$tmp/renode.tar.gz" "$RENODE_URL"
  else wget -q --show-progress -O "$tmp/renode.tar.gz" "$RENODE_URL"; fi
  mkdir -p "$RENODE_DIR"
  tar -xzf "$tmp/renode.tar.gz" -C "$RENODE_DIR" --strip-components=1
  rm -rf "$tmp"
fi
echo "Renode: $("$RENODE_DIR/renode" --version 2>/dev/null | head -1)"

# --- 2. platform files -------------------------------------------------------------------------
CPU_SRC="$RENODE_DIR/platforms/cpus/silabs/efr32s2/efr32mg24.repl"
[ -f "$CPU_SRC" ] || { echo "this Renode build has no EFR32MG24 platform ($CPU_SRC)"; exit 1; }
python3 - "$CPU_SRC" "$RENODE_DIR/platforms/cpus/silabs/efr32s2/efr32mg24_userdata_mem.repl" <<'EOF'
import re, sys
src, dst = sys.argv[1], sys.argv[2]
txt = open(src).read()
new = "flashuserdata: Memory.ArrayMemory @ sysbus 0x0FE00000\n    size: 0x800\n"
txt2, n = re.subn(r"^flashuserdata:\s*Miscellaneous\.SiLabs\.EFR32xG24_FlashUserData\s*@\s*sysbus\s*<0x0FE00000,\s*\+0x7F>\s*\n",
                  new, txt, flags=re.M)
if n != 1:
    sys.exit(f"could not patch flashuserdata in {src} (found {n} matches); Renode's platform file changed")
open(dst, "w").write(txt2)
print("installed", dst)
EOF
cp "$REPO/sim/renode/brd4186c_userdata_mem.repl" "$RENODE_DIR/platforms/boards/silabs/"
echo "installed $RENODE_DIR/platforms/boards/silabs/brd4186c_userdata_mem.repl"

# --- 3. user-data image ----------------------------------------------------------------------
python3 "$REPO/sim/renode/make_userdata.py" "$REPO/sim/renode/userdata_sim.bin"

echo "$RENODE_DIR" > "$REPO/sim/.renode_dir"
echo
echo "Simulation setup complete. Start the NCPs with:  wsl -d Ubuntu --cd <repo> -- bash sim/run_ncps.sh"
