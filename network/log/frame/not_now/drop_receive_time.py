import os
import argparse
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


INVALID_BIG_THRESHOLD = 1e18


def to_numeric_clean(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    s = s.mask(np.isinf(s), np.nan)
    s = s.mask(s.abs() > INVALID_BIG_THRESHOLD, np.nan)
    return s


def classify_dropped_frame(df: pd.DataFrame) -> pd.Series:
    decode_start_invalid = df["decodeStartMs"].isna() | (df["decodeStartMs"] <= 0)
    decode_finish_invalid = df["decodeFinishMs"].isna() | (df["decodeFinishMs"] <= 0)
    fb_extract_invalid = df["frameBufferExtractTimeMs"].isna() | (df["frameBufferExtractTimeMs"] <= 0)
    return decode_start_invalid | decode_finish_invalid | fb_extract_invalid


def load_frame_records(frame_csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(frame_csv_path)
    df.columns = df.columns.str.strip()

    required_cols = [
        "frameId",
        "decodeStartMs",
        "decodeFinishMs",
        "frameBufferExtractTimeMs",
    ]

    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns in frame_records.csv: {missing}")

    for col in required_cols:
        df[col] = to_numeric_clean(df[col])

    df["frameId"] = pd.to_numeric(df["frameId"], errors="coerce")
    df = df.dropna(subset=["frameId"]).copy()
    df["frameId"] = df["frameId"].astype("int64")

    df["isDropped"] = classify_dropped_frame(df)

    df = df[["frameId", "isDropped"]].drop_duplicates(subset=["frameId"]).copy()
    return df


def load_packet_csv(packet_csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(packet_csv_path)
    df.columns = df.columns.str.strip()

    required_cols = [
        "frameId",
        "sequenceNumber",
        "receiveTimeMs",
    ]

    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns in frame_packets.csv: {missing}")

    df["frameId"] = pd.to_numeric(df["frameId"], errors="coerce")
    df["sequenceNumber"] = pd.to_numeric(df["sequenceNumber"], errors="coerce")
    df["receiveTimeMs"] = to_numeric_clean(df["receiveTimeMs"])

    df = df.dropna(subset=["frameId", "sequenceNumber", "receiveTimeMs"]).copy()
    df["frameId"] = df["frameId"].astype("int64")
    df["sequenceNumber"] = df["sequenceNumber"].astype("int64")

    df = df.sort_values(["frameId", "receiveTimeMs", "sequenceNumber"]).reset_index(drop=True)
    return df


def summarize_packets(packet_df: pd.DataFrame) -> pd.DataFrame:
    rows = []

    for frame_id, group in packet_df.groupby("frameId", sort=True):
        g = group.sort_values(["receiveTimeMs", "sequenceNumber"]).reset_index(drop=True)

        recv = g["receiveTimeMs"].to_numpy()
        seq = g["sequenceNumber"].to_numpy()

        packet_count = len(g)
        first_recv = recv[0]
        last_recv = recv[-1]
        packet_span_ms = last_recv - first_recv

        if packet_count >= 2:
            inter_packet_gap = np.diff(recv)
            mean_gap = float(np.mean(inter_packet_gap))
            median_gap = float(np.median(inter_packet_gap))
            max_gap = float(np.max(inter_packet_gap))
        else:
            mean_gap = np.nan
            median_gap = np.nan
            max_gap = np.nan

        seq_span = int(seq.max() - seq.min()) if packet_count >= 1 else np.nan

        rows.append(
            {
                "frameId": frame_id,
                "packetCount": packet_count,
                "firstPacketReceiveTimeMs": first_recv,
                "lastPacketReceiveTimeMs": last_recv,
                "packetReceiveSpanMs": packet_span_ms,
                "meanInterPacketGapMs": mean_gap,
                "medianInterPacketGapMs": median_gap,
                "maxInterPacketGapMs": max_gap,
                "sequenceSpan": seq_span,
            }
        )

    return pd.DataFrame(rows)


def print_group_summary(merged: pd.DataFrame) -> None:
    print("\n[INFO] frame counts by dropped status:")
    print(merged["isDropped"].value_counts(dropna=False))

    print("\n[INFO] packet timing summary by dropped status:")
    summary = merged.groupby("isDropped")[
        [
            "packetCount",
            "packetReceiveSpanMs",
            "meanInterPacketGapMs",
            "medianInterPacketGapMs",
            "maxInterPacketGapMs",
            "sequenceSpan",
        ]
    ].agg(["count", "mean", "median", "std", "min", "max"])

    print(summary)


def plot_packet_comparison(merged: pd.DataFrame, out_path: str) -> None:
    dropped = merged[merged["isDropped"]].copy()
    non_dropped = merged[~merged["isDropped"]].copy()

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    axes[0, 0].hist(non_dropped["packetCount"].dropna(), bins=30, alpha=0.7, label="non-dropped")
    axes[0, 0].hist(dropped["packetCount"].dropna(), bins=30, alpha=0.7, label="dropped")
    axes[0, 0].set_title("Packet Count per Frame")
    axes[0, 0].set_xlabel("packetCount")
    axes[0, 0].set_ylabel("count")
    axes[0, 0].grid(True, alpha=0.3)
    axes[0, 0].legend()

    axes[0, 1].hist(non_dropped["packetReceiveSpanMs"].dropna(), bins=30, alpha=0.7, label="non-dropped")
    axes[0, 1].hist(dropped["packetReceiveSpanMs"].dropna(), bins=30, alpha=0.7, label="dropped")
    axes[0, 1].set_title("Packet Receive Span per Frame")
    axes[0, 1].set_xlabel("packetReceiveSpanMs")
    axes[0, 1].set_ylabel("count")
    axes[0, 1].grid(True, alpha=0.3)
    axes[0, 1].legend()

    axes[1, 0].hist(non_dropped["meanInterPacketGapMs"].dropna(), bins=30, alpha=0.7, label="non-dropped")
    axes[1, 0].hist(dropped["meanInterPacketGapMs"].dropna(), bins=30, alpha=0.7, label="dropped")
    axes[1, 0].set_title("Mean Inter-Packet Gap per Frame")
    axes[1, 0].set_xlabel("meanInterPacketGapMs")
    axes[1, 0].set_ylabel("count")
    axes[1, 0].grid(True, alpha=0.3)
    axes[1, 0].legend()

    axes[1, 1].hist(non_dropped["maxInterPacketGapMs"].dropna(), bins=30, alpha=0.7, label="non-dropped")
    axes[1, 1].hist(dropped["maxInterPacketGapMs"].dropna(), bins=30, alpha=0.7, label="dropped")
    axes[1, 1].set_title("Max Inter-Packet Gap per Frame")
    axes[1, 1].set_xlabel("maxInterPacketGapMs")
    axes[1, 1].set_ylabel("count")
    axes[1, 1].grid(True, alpha=0.3)
    axes[1, 1].legend()

    plt.tight_layout()
    plt.savefig(out_path, dpi=200)
    plt.close()

    print(f"[INFO] Saved plot: {out_path}")


def save_merged_csv(merged: pd.DataFrame, out_csv_path: str) -> None:
    merged.to_csv(out_csv_path, index=False)
    print(f"[INFO] Saved merged CSV: {out_csv_path}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Analyze packet receive timing for dropped vs non-dropped frames"
    )
    parser.add_argument(
        "-pacing",
        type=int,
        default=0,
        help="Use pacing CSVs if set to 1. Default: 0",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.pacing == 1:
        frame_csv_path = "/home/n2sl/yeon/qos/network/log/frame/frame_records_pacing.csv"
        packet_csv_path = "/home/n2sl/yeon/qos/network/log/frame/frame_packets_pacing.csv"
        out_plot_path = "/home/n2sl/yeon/qos/network/log/frame/plots/packet_receive_analysis_pacing.png"
        out_csv_path = "/home/n2sl/yeon/qos/network/log/frame/packet_receive_analysis_pacing.csv"
    else:
        frame_csv_path = "/home/n2sl/yeon/qos/network/log/frame/frame_records.csv"
        packet_csv_path = "/home/n2sl/yeon/qos/network/log/frame/frame_packets.csv"
        out_plot_path = "/home/n2sl/yeon/qos/network/log/frame/plots/packet_receive_analysis.png"
        out_csv_path = "/home/n2sl/yeon/qos/network/log/frame/packet_receive_analysis.csv"

    os.makedirs(os.path.dirname(out_plot_path), exist_ok=True)

    print(f"[INFO] frame_csv_path: {frame_csv_path}")
    print(f"[INFO] packet_csv_path: {packet_csv_path}")

    frame_df = load_frame_records(frame_csv_path)
    packet_df = load_packet_csv(packet_csv_path)
    packet_summary_df = summarize_packets(packet_df)

    merged = pd.merge(packet_summary_df, frame_df, on="frameId", how="left")
    merged["isDropped"] = merged["isDropped"].fillna(False)

    print(f"[INFO] frame rows: {len(frame_df)}")
    print(f"[INFO] packet rows: {len(packet_df)}")
    print(f"[INFO] summarized frame rows: {len(packet_summary_df)}")

    print_group_summary(merged)
    save_merged_csv(merged, out_csv_path)
    plot_packet_comparison(merged, out_plot_path)


if __name__ == "__main__":
    main()