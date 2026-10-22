## RECEIVE TIME OF EACH PACKET

import os
import argparse
import numpy as np
import pandas as pd


INVALID_BIG_THRESHOLD = 1e18

DEFAULT_LATE_THRESHOLD_MS = -5.0

CLASS_SAFE_NORMAL = "SAFE_NORMAL"
CLASS_LATE_NORMAL = "LATE_NORMAL"
CLASS_DROPPED = "DROPPED"


def to_numeric_clean(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    s = s.mask(np.isinf(s), np.nan)
    s = s.mask(s.abs() > INVALID_BIG_THRESHOLD, np.nan)
    return s


def classify_dropped_frame(df: pd.DataFrame) -> pd.Series:
    required_pipeline_cols = [
        "frameBufferInsertTimeMs",
        "frameBufferExtractTimeMs",
        "decodeQueueInsertTimeMs",
        "decodeQueueExtractTimeMs",
        "decodeStartMs",
        "decodeFinishMs",
    ]

    dropped = pd.Series(False, index=df.index)

    for col in required_pipeline_cols:
        if col not in df.columns:
            raise ValueError(f"Missing required column for dropped classification: {col}")

        dropped = dropped | df[col].isna() | (df[col] <= 0)

    return dropped


def classify_frame_class(df: pd.DataFrame, late_threshold_ms: float) -> pd.Series:
    frame_class = pd.Series(CLASS_SAFE_NORMAL, index=df.index, dtype="object")

    dropped_mask = df["isDropped"] == True
    late_normal_mask = (
        (df["isDropped"] == False)
        & df["max_wait"].notna()
        & (df["max_wait"] <= late_threshold_ms)
    )

    frame_class.loc[late_normal_mask] = CLASS_LATE_NORMAL
    frame_class.loc[dropped_mask] = CLASS_DROPPED

    return frame_class


def compute_predecode_waiting_ms(df: pd.DataFrame) -> pd.Series:
    """
    preDecodeWaitingMs = frameBufferInsertTimeMs - receiveTimeMs

    기존 plot 코드와 동일한 처리:
    - receiveTimeMs는 있는데 frameBufferInsertTimeMs가 없거나 <= 0이면 0.0
    - timestamp 순서 문제로 음수가 나오면 0.0
    """
    predecode = pd.Series(np.nan, index=df.index, dtype="float64")

    valid_receive = (
        df["receiveTimeMs"].notna()
        & (df["receiveTimeMs"] > 0)
    )

    valid_fb_insert = (
        df["frameBufferInsertTimeMs"].notna()
        & (df["frameBufferInsertTimeMs"] > 0)
    )

    valid_predecode_mask = valid_receive & valid_fb_insert

    predecode.loc[valid_predecode_mask] = (
        df.loc[valid_predecode_mask, "frameBufferInsertTimeMs"]
        - df.loc[valid_predecode_mask, "receiveTimeMs"]
    )

    no_frame_buffer_insert_mask = valid_receive & (~valid_fb_insert)
    predecode.loc[no_frame_buffer_insert_mask] = 0.0

    predecode.loc[predecode < 0] = 0.0

    return predecode


def format_frame_id(value) -> str:
    if pd.isna(value):
        return "NaN"

    try:
        f = float(value)
        if f.is_integer():
            return str(int(f))
        return str(f)
    except Exception:
        return str(value)


def format_ms(value) -> str:
    if pd.isna(value):
        return "NaN"

    try:
        return f"{float(value):.3f}"
    except Exception:
        return str(value)

def load_packet_span_by_frame(packet_csv_path: str) -> pd.DataFrame:
    """
    frame_records.csv에서 frameId별 packet receiveTime span 계산.

    packet_span = max(receiveTimeMs) - min(receiveTimeMs)

    필요한 컬럼:
    - frameId
    - receiveTimeMs
    """
    packet_df = pd.read_csv(packet_csv_path, skipinitialspace=True)
    packet_df.columns = packet_df.columns.str.strip()

    required_cols = ["frameId", "receiveTimeMs"]
    missing = [c for c in required_cols if c not in packet_df.columns]
    if missing:
        raise ValueError(f"Missing required columns in packet CSV: {missing}")

    packet_df["frameId"] = to_numeric_clean(packet_df["frameId"])
    packet_df["receiveTimeMs"] = to_numeric_clean(packet_df["receiveTimeMs"])

    packet_df = packet_df.dropna(subset=["frameId", "receiveTimeMs"]).copy()

    packet_df = packet_df[packet_df["receiveTimeMs"] > 0].copy()

    span_df = (
        packet_df
        .groupby("frameId", as_index=False)
        .agg(
            firstPacketReceiveTimeMs=("receiveTimeMs", "min"),
            lastPacketReceiveTimeMs=("receiveTimeMs", "max"),
        )
    )

    span_df["packet_span"] = (
        span_df["lastPacketReceiveTimeMs"]
        - span_df["firstPacketReceiveTimeMs"]
    )

    span_df.loc[span_df["packet_span"] < 0, "packet_span"] = 0.0

    return span_df[["frameId", "packet_span"]]


def load_and_classify(csv_path: str, late_threshold_ms: float,  packet_csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path, skipinitialspace=True)
    df.columns = df.columns.str.strip()

    required_cols = [
        "frameId",
        "max_wait",

        # preDecodeWaitingMs 계산용
        "receiveTimeMs",
        "frameBufferInsertTimeMs",

        # dropped 판정용
        "frameBufferExtractTimeMs",
        "decodeQueueInsertTimeMs",
        "decodeQueueExtractTimeMs",
        "decodeStartMs",
        "decodeFinishMs",
    ]

    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns in CSV: {missing}")

    for col in required_cols:
        df[col] = to_numeric_clean(df[col])

    df = df.dropna(subset=["frameId"]).copy()

    df["preDecodeWaitingMs"] = compute_predecode_waiting_ms(df)

    df["isDropped"] = classify_dropped_frame(df)
    df["frameClass"] = classify_frame_class(df, late_threshold_ms)

    packet_span_df = load_packet_span_by_frame(packet_csv_path)

    df = df.merge(
        packet_span_df,
        on="frameId",
        how="left",
    )

    df = df.sort_values("frameId").reset_index(drop=True)

    return df


def write_late_drop_txt(
    df: pd.DataFrame,
    out_path: str,
    csv_path: str,
    late_threshold_ms: float,
) -> None:
    output_cols = ["frameId", "max_wait", "preDecodeWaitingMs", "packet_span"]

    late_df = df[df["frameClass"] == CLASS_LATE_NORMAL][output_cols].copy()
    drop_df = df[df["frameClass"] == CLASS_DROPPED][output_cols].copy()

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("Frame classification summary\n")
        f.write("============================\n")
        f.write(f"input_csv: {csv_path}\n")
        f.write(f"late_threshold_ms: {late_threshold_ms}\n")
        f.write(f"total_frames: {len(df)}\n")
        f.write(f"late_normal_count: {len(late_df)}\n")
        f.write(f"dropped_count: {len(drop_df)}\n")
        f.write("\n")

        f.write("[LATE_NORMAL]\n")
        f.write("frameId,max_wait,preDecodeWaitingMs,packet_span\n")
        for _, row in late_df.iterrows():
            f.write(
                f"{format_frame_id(row['frameId'])},"
                f"{format_ms(row['max_wait'])},"
                f"{format_ms(row['preDecodeWaitingMs'])},"
                f"{format_ms(row['packet_span'])}\n"
            )

        f.write("\n")

        f.write("[DROPPED]\n")
        f.write("frameId,max_wait,preDecodeWaitingMs,packet_span\n")
        for _, row in drop_df.iterrows():
            f.write(
                f"{format_frame_id(row['frameId'])},"
                f"{format_ms(row['max_wait'])},"
                f"{format_ms(row['preDecodeWaitingMs'])},"
                f"{format_ms(row['packet_span'])}\n"
            )

    print(f"[INFO] saved: {out_path}")
    print(f"[INFO] total frames       : {len(df)}")
    print(f"[INFO] late normal count : {len(late_df)}")
    print(f"[INFO] dropped count     : {len(drop_df)}")


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Extract LATE_NORMAL and DROPPED frameId/max_wait/preDecodeWaitingMs "
            "from frame_records CSV."
        )
    )

    parser.add_argument(
        "-pacing",
        type=int,
        default=0,
        help="Use pacing CSV if set to 1. Default: 0",
    )

    parser.add_argument(
        "--late-threshold-ms",
        type=float,
        default=DEFAULT_LATE_THRESHOLD_MS,
        help="Threshold for LATE_NORMAL classification. Default: -5 ms",
    )

    parser.add_argument(
        "--input",
        type=str,
        default=None,
        help="Optional input CSV path",
    )

    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Optional output txt path",
    )

    parser.add_argument(
        "--input2",
        type=str,
        default=None,
        help="Packet-level CSV path used to compute packet_span",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    if args.input is not None:
        csv_path = args.input
    else:
        if args.pacing == 1:
            csv_path = "csv/frame_records_pacing.csv"  
        else:
            csv_path = "csv/frame_records.csv"
    
    if args.input2 is not None:
        csv_path2 = args.input2
    else:
        if args.pacing == 1:
            csv_path2 = "csv/frame_packets.csv"
        else:
            csv_path2 = "csv/frame_packets.csv"

    if args.output is not None:
        out_path = args.output
    else:
        if args.pacing == 1:
            out_path = "txt/late_drop_frames_pacing.txt"
        else:
            out_path = "txt/late_drop_frames.txt"

    print(f"[INFO] csv_path          : {csv_path}")
    print(f"[INFO] out_path          : {out_path}")
    print(f"[INFO] late_threshold_ms : {args.late_threshold_ms}")

    df = load_and_classify(
        csv_path=csv_path,
        late_threshold_ms=args.late_threshold_ms,
        packet_csv_path=csv_path2,
    )

    write_late_drop_txt(
        df=df,
        out_path=out_path,
        csv_path=csv_path,
        late_threshold_ms=args.late_threshold_ms,
    )


if __name__ == "__main__":
    main()
