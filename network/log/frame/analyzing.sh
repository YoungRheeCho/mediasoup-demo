#!/bin/bash

echo "===== <DRAW PLOTS> ====="
python3 draw_plots.py
echo ""

echo "===== <PARSING FRAME DROP LOG> ====="
python3 parse_frame_drop.py
echo ""

echo "===== <RECEIVE TIME OF EACH PACKET> ====="
python3 receive_time_of_packet.py
echo ""

echo "===== <ANALYZE LATE/DROP CAUSES> ====="
python3 analyze_late_drop_causes.py
echo ""

echo "===== <COMPARE PCAP AND RECEIVER PACKETS> ====="
python3 compare_pcap_receiver.py
python3 summary_pacp.py
python3 pacp.py
echo ""

echo "===== <DONE> ====="
