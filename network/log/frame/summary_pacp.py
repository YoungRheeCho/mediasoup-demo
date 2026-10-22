import os
import pandas as pd


INPUT_CSV = "csv/pcap_receiver_packet_compare.csv"
OUTPUT_TXT = "tshark/pcap_packet_span_simple.txt"

COLUMNS = [
    "frameId",
    "pcap_packet_count",
    "pcap_packet_span_ms",
    "receiver_packet_span_ms",
]


def main():
    df = pd.read_csv(INPUT_CSV)
    df.columns = df.columns.str.strip()

    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    out_df = df[COLUMNS].copy()

    # 보기 좋게 숫자 포맷 정리
    out_df["frameId"] = pd.to_numeric(out_df["frameId"], errors="coerce").astype("Int64")
    out_df["pcap_packet_count"] = pd.to_numeric(out_df["pcap_packet_count"], errors="coerce").astype("Int64")
    out_df["pcap_packet_span_ms"] = pd.to_numeric(out_df["pcap_packet_span_ms"], errors="coerce").round(3)
    out_df["receiver_packet_span_ms"] = pd.to_numeric(out_df["receiver_packet_span_ms"], errors="coerce").round(3)

    out_dir = os.path.dirname(OUTPUT_TXT)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with open(OUTPUT_TXT, "w", encoding="utf-8") as f:
        f.write(
            out_df.to_string(
                index=False,
                col_space=24,
                justify="right",
            )
        )
        f.write("\n")

    print(f"[INFO] saved: {OUTPUT_TXT}")
    print(f"[INFO] rows : {len(out_df)}")


if __name__ == "__main__":
    main()