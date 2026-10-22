import os
import argparse
import numpy as np
import pandas as pd


INVALID_BIG_THRESHOLD = 1e18


def to_numeric_clean(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    s = s.mask(np.isinf(s), np.nan)
    s = s.mask(s.abs() > INVALID_BIG_THRESHOLD, np.nan)
    return s


def compute_predecode_waiting_ms(df: pd.DataFrame) -> pd.Series:
    """
    preDecodeWaitingMs = frameBufferInsertTimeMs - receiveTimeMs

    기존 plot 코드와 동일한 처리:
    - receiveTimeMs가 유효하고 frameBufferInsertTimeMs도 유효하면 차이 계산
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


def load_and_compute(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path, skipinitialspace=True)
    df.columns = df.columns.str.strip()

    required_cols = [
        "frameId",
        "receiveTimeMs",
        "frameBufferInsertTimeMs",
    ]

    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns in CSV: {missing}")

    for col in required_cols:
        df[col] = to_numeric_clean(df[col])

    # frameId 없는 row는 제외
    df = df.dropna(subset=["frameId"]).copy()

    df["preDecodeWaitingMs"] = compute_predecode_waiting_ms(df)

    df = df.sort_values("frameId").reset_index(drop=True)

    return df


def write_all_frames_predecode_txt(
    df: pd.DataFrame,
    out_path: str,
    csv_path: str,
) -> None:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    valid_count = df["preDecodeWaitingMs"].notna().sum()

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("All frame preDecodeWaitingMs summary\n")
        f.write("====================================\n")
        f.write(f"input_csv: {csv_path}\n")
        f.write(f"total_frames: {len(df)}\n")
        f.write(f"valid_preDecodeWaitingMs_count: {valid_count}\n")
        f.write("\n")

        f.write("[ALL_FRAMES]\n")
        f.write("frameId,preDecodeWaitingMs\n")

        for _, row in df.iterrows():
            f.write(
                f"{format_frame_id(row['frameId'])},"
                f"{format_ms(row['preDecodeWaitingMs'])}\n"
            )

    print(f"[INFO] saved: {out_path}")
    print(f"[INFO] total frames: {len(df)}")
    print(f"[INFO] valid preDecodeWaitingMs: {valid_count}")


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Extract frameId and preDecodeWaitingMs for all frames "
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

    return parser.parse_args()


def main():
    args = parse_args()

    if args.input is not None:
        csv_path = args.input
    else:
        if args.pacing == 1:
            csv_path = "/home/n2sl/yeon/qos/network/log/frame/frame_records_pacing.csv"
        else:
            csv_path = "/home/n2sl/yeon/qos/network/log/frame/frame_records-1.csv"

    if args.output is not None:
        out_path = args.output
    else:
        if args.pacing == 1:
            out_path = "/home/n2sl/yeon/qos/network/log/frame/all_frames_predecode_pacing.txt"
        else:
            out_path = "/home/n2sl/yeon/qos/network/log/frame/all_frames_predecode.txt"

    print(f"[INFO] csv_path : {csv_path}")
    print(f"[INFO] out_path : {out_path}")

    df = load_and_compute(csv_path)

    write_all_frames_predecode_txt(
        df=df,
        out_path=out_path,
        csv_path=csv_path,
    )


if __name__ == "__main__":
    main()