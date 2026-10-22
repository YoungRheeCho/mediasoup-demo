#!/usr/bin/env bash
set -e

IFACE="ens15f0"
BASE_LOSS="3%"
DELAY="50ms"

cleanup() {
  echo "[INFO] cleanup: restore base loss only"
  sudo tc qdisc replace dev "${IFACE}" root netem loss "${BASE_LOSS}" 2>/dev/null || true
}

trap cleanup EXIT

echo "[INFO] start experiment on ${IFACE}"

echo "[INFO] phase 1: base loss only (${BASE_LOSS})"
sudo tc qdisc replace dev "${IFACE}" root netem loss "${BASE_LOSS}"
sleep 60

echo "[INFO] phase 2: base loss + delay (${BASE_LOSS}, ${DELAY})"
sudo tc qdisc replace dev "${IFACE}" root netem loss "${BASE_LOSS}" delay "${DELAY}"
sleep 60

echo "[INFO] phase 3: base loss only (${BASE_LOSS})"
sudo tc qdisc replace dev "${IFACE}" root netem loss "${BASE_LOSS}"
sleep 60

echo "[INFO] experiment finished"
sudo tc qdisc replace dev "${IFACE}" root netem loss "${BASE_LOSS}"