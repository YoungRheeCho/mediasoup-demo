#!/bin/bash

set -e

IFACE="ens15f0"

BASE_DIR="/home/n2sl/yeon/qos/network/log/frame"
DIR="$BASE_DIR/tshark"

PCAP_TMP="/tmp/webrtc.pcapng"
PCAP="$DIR/webrtc.pcapng"
RTP_CSV="$DIR/rtp_packets.csv"
UDP_CONV="$DIR/udp_conversations.txt"

MIN_BYTES=$((1024 * 1024))   # 1MB 이상 flow만 media 후보로 사용

mkdir -p "$DIR"

echo "[INFO] iface      : $IFACE"
echo "[INFO] tmp pcap   : $PCAP_TMP"
echo "[INFO] final pcap : $PCAP"
echo "[INFO] rtp csv    : $RTP_CSV"
echo "[INFO] min bytes  : $MIN_BYTES"
echo "[INFO] press Ctrl+C to stop capture and extract RTP csv"
echo ""

rm -f "$PCAP_TMP"

echo "[INFO] starting tshark..."
sudo tshark -i "$IFACE" -f "udp" -w "$PCAP_TMP" || true

echo ""
echo "[INFO] tshark stopped"

if [ ! -f "$PCAP_TMP" ]; then
	echo "[ERROR] pcap was not created: $PCAP_TMP"
	exit 1
fi

echo "[INFO] fixing pcap ownership..."
sudo chown "$USER:$USER" "$PCAP_TMP"

echo "[INFO] copying pcap to final directory..."
mv "$PCAP_TMP" "$PCAP"

echo "[INFO] saving UDP conversation summary..."
tshark -r "$PCAP" -q -z conv,udp | tee "$UDP_CONV"

echo ""
echo "[INFO] finding candidate media UDP ports..."

PORTS=$(
tshark -r "$PCAP" \
  -Y "udp" \
  -T fields \
  -e ip.src \
  -e udp.srcport \
  -e ip.dst \
  -e udp.dstport \
  -e frame.len |
awk -v min_bytes="$MIN_BYTES" '
{
  src_ip=$1
  src_port=$2
  dst_ip=$3
  dst_port=$4
  len=$5

  if (src_port == "" || dst_port == "" || len == "")
    next

  # 양방향 flow를 같은 key로 묶기 위해 endpoint 정렬
  ep1=src_ip ":" src_port
  ep2=dst_ip ":" dst_port

  if (ep1 < ep2)
    key=ep1 " <-> " ep2
  else
    key=ep2 " <-> " ep1

  bytes[key] += len
  sport[key] = src_port
  dport[key] = dst_port
}
END {
  for (k in bytes) {
    if (bytes[k] >= min_bytes) {
      print sport[k]
      print dport[k]
    }
  }
}' | sort -n | uniq
)

if [ -z "$PORTS" ]; then
	echo "[ERROR] no large UDP media candidate ports found"
	echo "[ERROR] check $UDP_CONV"
	exit 1
fi

echo "[INFO] candidate RTP decode ports:"
echo "$PORTS"

DECODE_ARGS=()
for PORT in $PORTS; do
	DECODE_ARGS+=("-d" "udp.port==$PORT,rtp")
done

echo ""
echo "[INFO] extracting RTP packets from pcap with auto decode-as..."

tshark -r "$PCAP" \
  "${DECODE_ARGS[@]}" \
  -Y "rtp" \
  -T fields \
  -E header=y \
  -E separator=, \
  -E quote=d \
  -e frame.number \
  -e frame.time_epoch \
  -e ip.src \
  -e ip.dst \
  -e udp.srcport \
  -e udp.dstport \
  -e rtp.ssrc \
  -e rtp.seq \
  -e rtp.timestamp \
  -e rtp.marker \
  -e rtp.p_type \
  -e frame.len \
  -e udp.length \
  > "$RTP_CSV"

echo ""
echo "[INFO] saved pcap       : $PCAP"
echo "[INFO] saved udp summary: $UDP_CONV"
echo "[INFO] saved RTP csv    : $RTP_CSV"
echo "[INFO] RTP csv lines:"
wc -l "$RTP_CSV"
echo "[INFO] done"
