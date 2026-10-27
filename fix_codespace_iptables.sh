#!/usr/bin/env bash
# Allow traffic between containers on Docker Compose bridge networks.
#
# This codespace has stale iptables-legacy tables alongside the nftables rules
# Docker manages. The legacy FORWARD chain defaults to DROP and only allows
# docker0, so traffic on Compose bridges (br-*) is silently dropped, for example
# when the databridge container connects to sftp:22. The rules are not
# persistent; run this again after a reboot.
set -euo pipefail

for direction in -i -o; do
  if ! sudo iptables-legacy -C FORWARD "$direction" br-+ -j ACCEPT 2>/dev/null; then
    sudo iptables-legacy -I FORWARD "$direction" br-+ -j ACCEPT
  fi
done

sudo iptables-legacy -S FORWARD
