import os
import argparse
import numpy as np
import pandas as pd


INVALID_BIG_THRESHOLD = 1e18

DEFAULT_FRAME_CSV_PATH = "/home/n2sl/yeon/qos/network/log/frame/frame_records.csv"
DEFAULT_FRAME_CSV_PATH_PACING = "/home/n2sl/yeon/qos/network/log/frame/frame_records_pacing.csv"

DEFAULT_SUMMARY_PATH = "/home/n2sl/yeon/qos/network/log/frame/plots/receive_predecode_summary.txt"
DEFAULT_SUMMARY_PATH_PACING = "/home/n2sl/yeon/qos/network/log/frame/plots/receive_predecode_summary_pacing.txt"

DEFAULT_LATE_THRESHOLD_MS = -5.0

CLASS_SAFE_NORMAL = "SAFE_NORMAL"
CLASS_LATE_NORMAL = "LATE_NORMAL"
CLASS_DROPPED = "DROPPED"

GROUP_RECEIVE_OK_INSERT_LATE_OR_MISSING = "RECEIVE_OK__FB_INSERT_AFTER_RENDER_OR_MISSING"
GROUP_RECEIVE_OK_INSERT_OK = "RECEIVE_OK__FB_INSERT_BEFORE_OR_ON_RENDER"
GROUP_RECEIVE_LATE = "RECEIVE_LATE"
GROUP_INVALID_RENDER = "INVALID_RENDER_COMPARE"

GROUP_ORDER = [
    GROUP_RECEIVE_OK_INSERT_LATE_OR_MISSING,
    GROUP_RECEIVE_OK_INSERT_OK,
    GROUP_RECEIVE_LATE,
    GROUP_INVALID_RENDER,
]


def to_numeric_clean(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    s = s.mask(np.isinf(s), np.nan)
    s = s.mask(s.abs() > INVALID_BIG_THRESHOLD, np.nan)
    return s


def valid_time(df: pd.DataFrame, col: str) -> pd.Series:
    return df[col].notna() & (df[col] > 0)


def classify_dropped_frame(df: pd.DataFrame) -> pd.Series:
    pipeline_cols = [
        "frameBufferInsertTimeMs",
        "frameBufferExtractTimeMs",
        "decodeQueueInsertTimeMs",
        "decodeQueueExtractTimeMs",
        "decodeStartMs",
        "decodeFinishMs",
    ]

    dropped = pd.Series(False, index=df.index)

    for col in pipeline_cols:
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


def classify_analysis_group(row: pd.Series) -> str:
    render_minus_receive = row["renderMinusReceiveMs"]

    if pd.isna(render_minus_receive):
        return GROUP_INVALID_RENDER

    receive_late = render_minus_receive < 0

    if receive_late:
        return GROUP_RECEIVE_LATE

    has_fb_insert = bool(row["hasFrameBufferInsert"])

    if not has_fb_insert:
        return GROUP_RECEIVE_OK_INSERT_LATE_OR_MISSING

    render_minus_insert = row["renderMinusFrameBufferInsertMs"]

    if pd.isna(render_minus_insert):
        return GROUP_RECEIVE_OK_INSERT_LATE_OR_MISSING

    # render_time - frameBufferInsertTimeMs < 0
    # => frameBufferInsert가 render_time보다 늦음
    if render_minus_insert < 0:
        return GROUP_RECEIVE_OK_INSERT_LATE_OR_MISSING

    # render_time - frameBufferInsertTimeMs >= 0
    # => frameBufferInsert가 render_time보다 빠르거나 같음
    return GROUP_RECEIVE_OK_INSERT_OK


def safe_diff(df: pd.DataFrame, end_col: str, start_col: str) -> pd.Series:
    out = pd.Series(np.nan, index=df.index, dtype="float64")

    valid = valid_time(df, end_col) & valid_time(df, start_col)
    out.loc[valid] = df.loc[valid, end_col] - df.loc[valid, start_col]

    out.loc[out < 0] = np.nan
    return out


def load_and_prepare_csv(csv_path: str, late_threshold_ms: float) -> pd.DataFrame:
    df = pd.read_csv(csv_path, skipinitialspace=True)
    df.columns = df.columns.str.strip()

    required_cols = [
        "frameId",
        "receiveTimeMs",
        "render_time",
        "frameBufferInsertTimeMs",
        "frameBufferExtractTimeMs",
        "decodeQueueInsertTimeMs",
        "decodeQueueExtractTimeMs",
        "decodeStartMs",
        "decodeFinishMs",
        "max_wait",
        "decodeSlackNominalMs",
    ]

    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    for col in required_cols:
        df[col] = to_numeric_clean(df[col])

    df = df.dropna(subset=["frameId"]).copy()

    # frame class
    df["isDropped"] = classify_dropped_frame(df)
    df["frameClass"] = classify_frame_class(df, late_threshold_ms)

    # drop breakdown
    df["hasFrameBufferInsert"] = valid_time(df, "frameBufferInsertTimeMs")

    df["dropNoFrameBufferInsert"] = (
        (df["isDropped"] == True)
        & (~df["hasFrameBufferInsert"])
    )

    df["dropAfterFrameBufferInsert"] = (
        (df["isDropped"] == True)
        & (df["hasFrameBufferInsert"])
    )

    # render_time - receiveTimeMs
    df["renderMinusReceiveMs"] = np.nan
    valid_render_receive = valid_time(df, "render_time") & valid_time(df, "receiveTimeMs")
    df.loc[valid_render_receive, "renderMinusReceiveMs"] = (
        df.loc[valid_render_receive, "render_time"]
        - df.loc[valid_render_receive, "receiveTimeMs"]
    )

    # render_time - frameBufferInsertTimeMs
    df["renderMinusFrameBufferInsertMs"] = np.nan
    valid_render_insert = valid_time(df, "render_time") & valid_time(df, "frameBufferInsertTimeMs")
    df.loc[valid_render_insert, "renderMinusFrameBufferInsertMs"] = (
        df.loc[valid_render_insert, "render_time"]
        - df.loc[valid_render_insert, "frameBufferInsertTimeMs"]
    )

    # preDecodeWaitingMs = frameBufferInsertTimeMs - receiveTimeMs
    df["preDecodeWaitingMs"] = np.nan
    valid_predecode = valid_time(df, "frameBufferInsertTimeMs") & valid_time(df, "receiveTimeMs")
    df.loc[valid_predecode, "preDecodeWaitingMs"] = (
        df.loc[valid_predecode, "frameBufferInsertTimeMs"]
        - df.loc[valid_predecode, "receiveTimeMs"]
    )
    df.loc[df["preDecodeWaitingMs"] < 0, "preDecodeWaitingMs"] = np.nan

    # decodeSlackNominal invalid 제거
    df.loc[df["decodeSlackNominalMs"].abs() > 1e12, "decodeSlackNominalMs"] = np.nan

    # queueResidenceMs = frameBufferResidence + decodeQueueResidence
    # 가능한 프레임만 계산
    df["frameBufferResidenceComputedMs"] = safe_diff(
        df,
        end_col="frameBufferExtractTimeMs",
        start_col="frameBufferInsertTimeMs",
    )

    df["decodeQueueResidenceComputedMs"] = safe_diff(
        df,
        end_col="decodeQueueExtractTimeMs",
        start_col="decodeQueueInsertTimeMs",
    )

    df["queueResidenceMs"] = np.nan
    valid_queue = (
        df["frameBufferResidenceComputedMs"].notna()
        & df["decodeQueueResidenceComputedMs"].notna()
    )
    df.loc[valid_queue, "queueResidenceMs"] = (
        df.loc[valid_queue, "frameBufferResidenceComputedMs"]
        + df.loc[valid_queue, "decodeQueueResidenceComputedMs"]
    )

    # late/drop 분석용 group
    df["analysisGroup"] = df.apply(classify_analysis_group, axis=1)

    return df


def fmt(v) -> str:
    if pd.isna(v):
        return "NaN"
    return f"{v:.3f}"


def metric_stats(series: pd.Series) -> dict:
    s = series.dropna()

    if len(s) == 0:
        return {
            "valid": 0,
            "mean": np.nan,
            "median": np.nan,
            "min": np.nan,
            "max": np.nan,
            "p95": np.nan,
        }

    return {
        "valid": int(len(s)),
        "mean": float(s.mean()),
        "median": float(s.median()),
        "min": float(s.min()),
        "max": float(s.max()),
        "p95": float(s.quantile(0.95)),
    }


def write_metric(f, label: str, series: pd.Series, unit: str = "ms") -> None:
    s = metric_stats(series)

    f.write(
        f"  {label:<36} "
        f"valid={s['valid']:<6} "
        f"mean={fmt(s['mean']):>10} {unit:<3} "
        f"median={fmt(s['median']):>10} {unit:<3} "
        f"min={fmt(s['min']):>10} {unit:<3} "
        f"max={fmt(s['max']):>10} {unit:<3} "
        f"p95={fmt(s['p95']):>10} {unit:<3}\n"
    )


def group_description(group: str) -> str:
    if group == GROUP_RECEIVE_OK_INSERT_LATE_OR_MISSING:
        return (
            "receiveTimeMs <= render_time, and "
            "frameBufferInsertTimeMs > render_time OR frameBufferInsertTimeMs is missing"
        )

    if group == GROUP_RECEIVE_OK_INSERT_OK:
        return (
            "receiveTimeMs <= render_time, and "
            "frameBufferInsertTimeMs <= render_time"
        )

    if group == GROUP_RECEIVE_LATE:
        return "receiveTimeMs > render_time"

    if group == GROUP_INVALID_RENDER:
        return (
            "render_time or receiveTimeMs is missing/zero; "
            "receive-vs-render comparison unavailable"
        )

    return ""


def write_safe_normal_section(f, df: pd.DataFrame) -> None:
    normal_df = df[df["frameClass"] == CLASS_SAFE_NORMAL]

    f.write("=" * 120 + "\n")
    f.write("[SAFE_NORMAL]\n")
    f.write("Frames that are neither late normal nor dropped.\n")
    f.write("No late/drop breakdown is printed here; only requested metrics are summarized.\n")
    f.write("-" * 120 + "\n\n")

    if len(normal_df) == 0:
        f.write("No SAFE_NORMAL frames.\n\n")
        return

    f.write("[Timing reference]\n")
    write_metric(
        f,
        "render_time - receiveTimeMs",
        normal_df["renderMinusReceiveMs"],
    )
    write_metric(
        f,
        "render_time - frameBufferInsert",
        normal_df["renderMinusFrameBufferInsertMs"],
    )
    f.write("\n")

    f.write("[Requested metrics]\n")
    write_metric(
        f,
        "preDecodeWaitingMs",
        normal_df["preDecodeWaitingMs"],
    )
    write_metric(
        f,
        "decodeSlackNominalMs",
        normal_df["decodeSlackNominalMs"],
    )
    write_metric(
        f,
        "queueResidenceMs",
        normal_df["queueResidenceMs"],
    )
    f.write("\n\n")


def save_summary_txt(df: pd.DataFrame, summary_path: str, late_threshold_ms: float) -> None:
    os.makedirs(os.path.dirname(summary_path), exist_ok=True)

    late_drop_df = df[df["frameClass"].isin([CLASS_LATE_NORMAL, CLASS_DROPPED])].copy()

    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("Receive / predecode summary\n")
        f.write("===========================\n\n")

        f.write("[Frame class]\n")
        f.write(f"SAFE_NORMAL : non-dropped and max_wait >  {late_threshold_ms} ms\n")
        f.write(f"LATE_NORMAL : non-dropped and max_wait <= {late_threshold_ms} ms\n")
        f.write("DROPPED     : one or more pipeline timestamps are missing/zero\n\n")

        f.write("[Metric sign convention]\n")
        f.write("render_time - receiveTimeMs\n")
        f.write("  >= 0 : receive happened before/on render_time\n")
        f.write("  <  0 : receive happened after render_time\n\n")

        f.write("render_time - frameBufferInsertTimeMs\n")
        f.write("  >= 0 : frameBufferInsert happened before/on render_time\n")
        f.write("  <  0 : frameBufferInsert happened after render_time\n\n")

        # ============================================================
        # 추가된 정상 프레임 section
        # ============================================================
        write_safe_normal_section(f, df)

        # ============================================================
        # 기존 late/drop section
        # ============================================================
        f.write("=" * 120 + "\n")
        f.write("[LATE_NORMAL + DROPPED GROUP SUMMARY]\n")
        f.write("Existing late/drop analysis follows.\n")
        f.write("-" * 120 + "\n\n")

        f.write("[Overall late/drop counts]\n")
        f.write(f"total late/drop frames          : {len(late_drop_df)}\n")
        f.write(f"LATE_NORMAL                    : {int((late_drop_df['frameClass'] == CLASS_LATE_NORMAL).sum())}\n")
        f.write(f"DROPPED                        : {int((late_drop_df['frameClass'] == CLASS_DROPPED).sum())}\n")
        f.write(f"drop: no frame buffer insert   : {int(late_drop_df['dropNoFrameBufferInsert'].sum())}\n")
        f.write(f"drop: after frame buffer insert: {int(late_drop_df['dropAfterFrameBufferInsert'].sum())}\n")
        f.write("\n")

        f.write("[Group count table]\n")
        f.write(
            f"{'Group':<50} "
            f"{'Total':>8} "
            f"{'LateNormal':>12} "
            f"{'Dropped':>10} "
            f"{'DropNoFB':>10} "
            f"{'DropAfterFB':>12} "
            f"{'ValidPreDec':>12} "
            f"{'ValidQueue':>11}\n"
        )
        f.write("-" * 135 + "\n")

        for group in GROUP_ORDER:
            g = late_drop_df[late_drop_df["analysisGroup"] == group]

            f.write(
                f"{group:<50} "
                f"{len(g):>8} "
                f"{int((g['frameClass'] == CLASS_LATE_NORMAL).sum()):>12} "
                f"{int((g['frameClass'] == CLASS_DROPPED).sum()):>10} "
                f"{int(g['dropNoFrameBufferInsert'].sum()):>10} "
                f"{int(g['dropAfterFrameBufferInsert'].sum()):>12} "
                f"{int(g['preDecodeWaitingMs'].notna().sum()):>12} "
                f"{int(g['queueResidenceMs'].notna().sum()):>11}\n"
            )

        f.write("\n\n")

        for group in GROUP_ORDER:
            g = late_drop_df[late_drop_df["analysisGroup"] == group]

            f.write("=" * 120 + "\n")
            f.write(f"[{group}]\n")
            f.write(f"{group_description(group)}\n")
            f.write("-" * 120 + "\n")

            f.write(f"total frames                    : {len(g)}\n")
            f.write(f"late normal count               : {int((g['frameClass'] == CLASS_LATE_NORMAL).sum())}\n")
            f.write(f"dropped count                   : {int((g['frameClass'] == CLASS_DROPPED).sum())}\n")
            f.write(f"drop no frame buffer insert     : {int(g['dropNoFrameBufferInsert'].sum())}\n")
            f.write(f"drop after frame buffer insert  : {int(g['dropAfterFrameBufferInsert'].sum())}\n")
            f.write(f"valid preDecodeWaitingMs count  : {int(g['preDecodeWaitingMs'].notna().sum())}\n")
            f.write(f"valid decodeSlackNominal count  : {int(g['decodeSlackNominalMs'].notna().sum())}\n")
            f.write(f"valid queueResidenceMs count    : {int(g['queueResidenceMs'].notna().sum())}\n\n")

            if len(g) == 0:
                f.write("No frames in this group.\n\n")
                continue

            f.write("[Timing reference]\n")
            write_metric(
                f,
                "render_time - receiveTimeMs",
                g["renderMinusReceiveMs"],
            )
            write_metric(
                f,
                "render_time - frameBufferInsert",
                g["renderMinusFrameBufferInsertMs"],
            )
            f.write("\n")

            f.write("[Requested metrics]\n")
            write_metric(
                f,
                "preDecodeWaitingMs",
                g["preDecodeWaitingMs"],
            )
            write_metric(
                f,
                "decodeSlackNominalMs",
                g["decodeSlackNominalMs"],
            )
            write_metric(
                f,
                "queueResidenceMs",
                g["queueResidenceMs"],
            )
            f.write("\n")

    print(f"[INFO] Saved summary: {summary_path}")


def print_console_summary(df: pd.DataFrame) -> None:
    print("\n[OVERALL]")
    print(f"SAFE_NORMAL : {int((df['frameClass'] == CLASS_SAFE_NORMAL).sum())}")
    print(f"LATE_NORMAL : {int((df['frameClass'] == CLASS_LATE_NORMAL).sum())}")
    print(f"DROPPED     : {int((df['frameClass'] == CLASS_DROPPED).sum())}")

    late_drop_df = df[df["frameClass"].isin([CLASS_LATE_NORMAL, CLASS_DROPPED])]

    print("\n[LATE/DROP GROUP COUNTS]")
    for group in GROUP_ORDER:
        g = late_drop_df[late_drop_df["analysisGroup"] == group]
        print(
            f"{group:<50} "
            f"total={len(g):<6} "
            f"late={int((g['frameClass'] == CLASS_LATE_NORMAL).sum()):<6} "
            f"drop={int((g['frameClass'] == CLASS_DROPPED).sum()):<6} "
            f"dropNoFB={int(g['dropNoFrameBufferInsert'].sum()):<6} "
            f"dropAfterFB={int(g['dropAfterFrameBufferInsert'].sum()):<6}"
        )


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Summarize SAFE_NORMAL plus late-normal/dropped frames using "
            "receive-vs-render and frameBufferInsert-vs-render metrics."
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
        help="Optional custom frame_records CSV path",
    )

    parser.add_argument(
        "--summary-output",
        type=str,
        default=None,
        help="Optional output summary TXT path",
    )

    parser.add_argument(
        "--late-threshold-ms",
        type=float,
        default=DEFAULT_LATE_THRESHOLD_MS,
        help="Threshold for LATE_NORMAL classification. Default: -5 ms",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    if args.input is not None:
        csv_path = args.input
    else:
        if args.pacing == 1:
            csv_path = DEFAULT_FRAME_CSV_PATH_PACING
        else:
            csv_path = DEFAULT_FRAME_CSV_PATH

    if args.summary_output is not None:
        summary_path = args.summary_output
    else:
        if args.pacing == 1:
            summary_path = DEFAULT_SUMMARY_PATH_PACING
        else:
            summary_path = DEFAULT_SUMMARY_PATH

    print(f"[INFO] csv_path          : {csv_path}")
    print(f"[INFO] summary_path      : {summary_path}")
    print(f"[INFO] late_threshold_ms : {args.late_threshold_ms}")

    df = load_and_prepare_csv(
        csv_path=csv_path,
        late_threshold_ms=args.late_threshold_ms,
    )

    print_console_summary(df)

    save_summary_txt(
        df=df,
        summary_path=summary_path,
        late_threshold_ms=args.late_threshold_ms,
    )


if __name__ == "__main__":
    main()