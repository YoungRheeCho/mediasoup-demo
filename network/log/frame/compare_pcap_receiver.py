import pandas as pd
import numpy as np


FRAME_PACKETS_CSV = "csv/frame_packets.csv"
LATE_DROP_CAUSE_CSV = "csv/late_drop_cause_analysis.csv"
PCAP_RTP_CSV = "tshark/rtp_packets.csv"

OUT_CSV = "csv/pcap_receiver_packet_compare.csv"


def to_num(df, cols):
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def build_frame_summary(merged, out_csv, match_mode, timestamp_offset):
    pcap_frame = (
        merged.groupby("frameId")
        .agg(
            pcap_packet_count=("rtp.seq", "count"),
            pcap_first_epoch=("frame.time_epoch", "min"),
            pcap_last_epoch=("frame.time_epoch", "max"),
            recv_first_ms=("receiveTimeMs", "min"),
            recv_last_ms=("receiveTimeMs", "max"),
            first_seq=("sequenceNumber", "min"),
            last_seq=("sequenceNumber", "max"),
            frameClass=("frameClass", "first"),
            primaryCause=("primaryCause", "first"),
            max_wait=("max_wait", "first"),
            preDecodeWaitingMs=("preDecodeWaitingMs", "first"),
        )
        .reset_index()
    )

    pcap_frame["pcap_packet_span_ms"] = (
        pcap_frame["pcap_last_epoch"] - pcap_frame["pcap_first_epoch"]
    ) * 1000.0

    pcap_frame["receiver_packet_span_ms"] = (
        pcap_frame["recv_last_ms"] - pcap_frame["recv_first_ms"]
    )

    pcap_frame["pcap_matched"] = pcap_frame["pcap_packet_count"] > 0
    pcap_frame["match_mode"] = match_mode
    pcap_frame["timestamp_offset"] = timestamp_offset

    pcap_frame.to_csv(out_csv, index=False)

    return pcap_frame


def print_summary(pcap_frame):
    print(f"[INFO] saved: {OUT_CSV}")

    print("\n[INFO] pcap match summary:")
    print(pcap_frame["pcap_matched"].value_counts(dropna=False))

    total = len(pcap_frame)
    matched = int(pcap_frame["pcap_matched"].sum())
    ratio = matched / total * 100.0 if total > 0 else 0.0
    print(f"[INFO] matched frames: {matched}/{total} ({ratio:.2f}%)")

    print("\n[INFO] match mode:")
    print(pcap_frame["match_mode"].value_counts(dropna=False))

    print("\n[INFO] pcap_packet_span_ms summary for matched frames:")
    print(pcap_frame.loc[pcap_frame["pcap_matched"], "pcap_packet_span_ms"].describe())

    print("\n[INFO] receiver_packet_span_ms summary for matched frames:")
    print(pcap_frame.loc[pcap_frame["pcap_matched"], "receiver_packet_span_ms"].describe())

    print("\n[INFO] sample:")
    print(
        pcap_frame[
            [
                "frameId",
                "frameClass",
                "primaryCause",
                "max_wait",
                "preDecodeWaitingMs",
                "pcap_matched",
                "pcap_packet_span_ms",
                "receiver_packet_span_ms",
                "match_mode",
                "timestamp_offset",
            ]
        ]
        .head(40)
        .to_string(index=False)
    )


def estimate_best_offset(target_packets: pd.DataFrame, pcap: pd.DataFrame):
    """
    Fallback only.
    Used when direct:
        frameId == rtp.timestamp
        sequenceNumber == rtp.seq
    does not match.

    Estimate:
        offset = rtp.timestamp - frameId
    from sequenceNumber/rtp.seq overlap.
    """

    seq_join = target_packets.merge(
        pcap,
        left_on="sequenceNumber",
        right_on="rtp.seq",
        how="inner",
        suffixes=("_recv", "_pcap"),
    )

    if len(seq_join) == 0:
        return None

    seq_join["timestamp_offset"] = (
        seq_join["rtp.timestamp"] - seq_join["frameId"]
    )

    offset_counts = (
        seq_join["timestamp_offset"]
        .round()
        .astype("int64")
        .value_counts()
    )

    print("\n[INFO] fallback timestamp offset candidates:")
    print(offset_counts.head(10).to_string())

    return int(offset_counts.index[0])


def main():
    frame_packets = pd.read_csv(FRAME_PACKETS_CSV)
    late_drop = pd.read_csv(LATE_DROP_CAUSE_CSV)
    pcap = pd.read_csv(PCAP_RTP_CSV)

    frame_packets.columns = frame_packets.columns.str.strip()
    late_drop.columns = late_drop.columns.str.strip()
    pcap.columns = pcap.columns.str.strip()

    frame_packets = to_num(
        frame_packets,
        ["frameId", "sequenceNumber", "receiveTimeMs"],
    )

    late_drop = to_num(
        late_drop,
        ["frameId", "max_wait", "preDecodeWaitingMs"],
    )

    pcap = to_num(
        pcap,
        ["rtp.timestamp", "rtp.seq", "frame.time_epoch"],
    )

    frame_packets = frame_packets.dropna(
        subset=["frameId", "sequenceNumber", "receiveTimeMs"]
    ).copy()

    late_drop = late_drop.dropna(
        subset=["frameId"]
    ).copy()

    pcap = pcap.dropna(
        subset=["rtp.timestamp", "rtp.seq", "frame.time_epoch"]
    ).copy()

    frame_packets["frameId"] = frame_packets["frameId"].astype("int64")
    frame_packets["sequenceNumber"] = frame_packets["sequenceNumber"].astype("int64")
    late_drop["frameId"] = late_drop["frameId"].astype("int64")
    pcap["rtp.timestamp"] = pcap["rtp.timestamp"].astype("int64")
    pcap["rtp.seq"] = pcap["rtp.seq"].astype("int64")

    target_packets = frame_packets.merge(
        late_drop[
            [
                "frameId",
                "frameClass",
                "primaryCause",
                "max_wait",
                "preDecodeWaitingMs",
            ]
        ],
        on="frameId",
        how="inner",
    )

    print(f"[INFO] target late/drop frames : {target_packets['frameId'].nunique()}")
    print(f"[INFO] target packet rows      : {len(target_packets)}")
    print(f"[INFO] pcap rows               : {len(pcap)}")

    # ============================================================
    # 1. First try direct match.
    #    This is the correct path when:
    #      frameId == rtp.timestamp
    #      sequenceNumber == rtp.seq
    # ============================================================
    direct_merged = target_packets.merge(
        pcap,
        left_on=["frameId", "sequenceNumber"],
        right_on=["rtp.timestamp", "rtp.seq"],
        how="left",
    )

    direct_frame = build_frame_summary(
        merged=direct_merged,
        out_csv=OUT_CSV,
        match_mode="direct",
        timestamp_offset=0,
    )

    direct_matched = int(direct_frame["pcap_matched"].sum())
    direct_total = len(direct_frame)
    direct_ratio = direct_matched / direct_total * 100.0 if direct_total > 0 else 0.0

    print("\n[INFO] direct match result:")
    print(f"[INFO] matched frames: {direct_matched}/{direct_total} ({direct_ratio:.2f}%)")

    if direct_matched > 0:
        print("[INFO] using direct match: frameId == rtp.timestamp")
        print_summary(direct_frame)
        return

    # ============================================================
    # 2. Fallback: estimate timestamp offset.
    # ============================================================
    print("\n[WARN] direct match found 0 frames.")
    print("[WARN] trying timestamp offset fallback...")

    timestamp_offset = estimate_best_offset(target_packets, pcap)

    if timestamp_offset is None:
        print("[ERROR] no overlapping sequenceNumber/rtp.seq.")
        print("[ERROR] PCAP and telemetry are not aligned.")
        print_summary(direct_frame)
        return

    target_packets = target_packets.copy()
    target_packets["pcap_timestamp_est"] = (
        target_packets["frameId"] + timestamp_offset
    ).astype("int64")

    offset_merged = target_packets.merge(
        pcap,
        left_on=["pcap_timestamp_est", "sequenceNumber"],
        right_on=["rtp.timestamp", "rtp.seq"],
        how="left",
    )

    offset_frame = build_frame_summary(
        merged=offset_merged,
        out_csv=OUT_CSV,
        match_mode="offset",
        timestamp_offset=timestamp_offset,
    )

    offset_matched = int(offset_frame["pcap_matched"].sum())
    offset_total = len(offset_frame)
    offset_ratio = offset_matched / offset_total * 100.0 if offset_total > 0 else 0.0

    print("\n[INFO] offset match result:")
    print(f"[INFO] selected timestamp offset: {timestamp_offset}")
    print(f"[INFO] matched frames: {offset_matched}/{offset_total} ({offset_ratio:.2f}%)")

    if offset_matched == 0:
        print("[ERROR] offset fallback also matched 0 frames.")
        print("[ERROR] PCAP and telemetry are probably not aligned.")

    print_summary(offset_frame)


if __name__ == "__main__":
    main()
