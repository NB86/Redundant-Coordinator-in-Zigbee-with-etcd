#!/bin/bash
# Start the two simulated NCPs (headless Renode). Run from the repository root:
#   wsl -d Ubuntu --cd <repo> -- bash sim/run_ncps.sh
# Coordinator NCP: tcp 24842, end-device NCP: tcp 24852, Renode monitor: telnet 24999.
set -euo pipefail
REPO="$(pwd)"
RENODE_DIR="${RENODE_DIR:-$(cat "$REPO/sim/.renode_dir" 2>/dev/null || echo "$HOME/renode_portable")}"
[ -x "$RENODE_DIR/renode" ] || { echo "Renode not found in $RENODE_DIR - run setup.bat (sim/setup_wsl.sh) first"; exit 1; }
[ -f "$REPO/sim/renode/userdata_sim.bin" ] || { echo "sim/renode/userdata_sim.bin missing - run setup.bat first"; exit 1; }
# Renode's monitor cannot parse paths containing spaces (common for Windows checkouts, e.g. "Semester 10"),
# so the simulation directory is reached through a space-free symlink and added to Renode's file search path.
LINK="$HOME/.zigbee-ha-sim"
ln -sfn "$REPO/sim" "$LINK"
exec "$RENODE_DIR/renode" --disable-xwt -P 24999 -e "path add @$LINK" -e "include @$LINK/renode/twonode.resc" < /dev/null
