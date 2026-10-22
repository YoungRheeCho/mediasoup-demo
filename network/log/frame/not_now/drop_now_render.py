import os
import argparse
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


INVALID_BIG_THRESHOLD = 1e18


# 3-class 기준값
DEFAULT_LATE_THRESHOLD_MS = -5.0


CLASS_SAFE_NORMAL = "SAFE_NORMAL"
CLASS_LATE_NORMAL = "LATE_NORMAL"
CLASS_DROPPED = "DROPPED"


CLASS_ORDER = [
    CLASS_SAFE_NORMAL,
    CLASS_LATE_NORMAL,
    CLASS_DROPPED,
]


CLASS_LABEL = {
    CLASS_SAFE_NORMAL: "safe normal",
    CLASS_LATE_NORMAL: "late normal",
    CLASS_DROPPED: "dropped",
}


CLASS_COLOR = {
    CLASS_SAFE_NORMAL: "tab:blue",
    CLASS_LATE_NORMAL: "tab:orange",
    CLASS_DROPPED: "tab:red",
}


CLASS_MARKER = {
    CLASS_SAFE_NORMAL: "o",
    CLASS_LATE_NORMAL: "^",
    CLASS_DROPPED: "x",
}


def to_numeric_clean(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    s = s.mask(np.isinf(s), np.nan)
    s = s.mask(s.abs() > INVALID_BIG_THRESHOLD, np.nan)
    return s


def classify_dropped_frame(df: pd.DataFrame) -> pd.Series:
    """
    드랍 프레임 판별 규칙.

    아래 중 하나라도 해당되면 dropped frame:
    - decodeStartMs <= 0
    - decodeFinishMs <= 0
    - frameBufferExtractTimeMs <= 0
    """
    decode_start_invalid = df["decodeStartMs"].isna() | (df["decodeStartMs"] <= 0)
    decode_finish_invalid = df["decodeFinishMs"].isna() | (df["decodeFinishMs"] <= 0)
    fb_extract_invalid = df["frameBufferExtractTimeMs"].isna() | (df["frameBufferExtractTimeMs"] <= 0)

    return decode_start_invalid | decode_finish_invalid | fb_extract_invalid


def classify_frame_type(df: pd.DataFrame, late_threshold_ms: float) -> pd.Series:
    """
    3가지 frame class 분류.

    1. SAFE_NORMAL:
       isDropped == 0 and max_wait > late_threshold_ms

    2. LATE_NORMAL:
       isDropped == 0 and max_wait <= late_threshold_ms
       즉, 늦었지만 현재 buffer의 마지막 decodable frame이라 drop되지 않고 decode 쪽으로 넘어간 경우로 해석

    3. DROPPED:
       isDropped == 1
    """
    frame_class = pd.Series(CLASS_SAFE_NORMAL, index=df.index, dtype="object")

    dropped_mask = df["isDropped"] == 1
    late_normal_mask = (df["isDropped"] == 0) & (df["max_wait"] <= late_threshold_ms)

    frame_class.loc[late_normal_mask] = CLASS_LATE_NORMAL
    frame_class.loc[dropped_mask] = CLASS_DROPPED

    return frame_class


def format_value(value) -> str:
    if pd.isna(value):
        return ""

    try:
        f = float(value)
        if f.is_integer():
            return str(int(f))
        return str(f)
    except Exception:
        return str(value)


def save_csv_with_space_after_comma(df: pd.DataFrame, output_csv_path: str) -> None:
    os.makedirs(os.path.dirname(output_csv_path), exist_ok=True)

    with open(output_csv_path, "w", encoding="utf-8") as f:
        f.write(", ".join(df.columns) + "\n")

        for _, row in df.iterrows():
            values = [format_value(row[col]) for col in df.columns]
            f.write(", ".join(values) + "\n")


def extract_timing_drop_csv(
    input_csv_path: str,
    output_csv_path: str,
    late_threshold_ms: float,
) -> pd.DataFrame:
    df = pd.read_csv(input_csv_path, skipinitialspace=True)
    df.columns = df.columns.str.strip()

    required_cols = [
        "frameId",
        "receiveTimeMs",
        "now",
        "render_time",
        "max_wait",
        "frameBufferInsertTimeMs",
        "frameBufferExtractTimeMs",
        "decodeStartMs",
        "decodeFinishMs",
    ]

    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns in CSV: {missing}")

    for col in required_cols:
        df[col] = to_numeric_clean(df[col])

    df["isDropped"] = classify_dropped_frame(df).astype(int)
    df["frameClass"] = classify_frame_type(df, late_threshold_ms)

    out_df = df[
        [
            "frameId",
            "receiveTimeMs",
            "frameBufferInsertTimeMs",
            "frameBufferExtractTimeMs",
            "now",
            "render_time",
            "max_wait",
            "isDropped",
            "frameClass",
        ]
    ].copy()

    save_csv_with_space_after_comma(out_df, output_csv_path)

    print(f"[INFO] extracted csv saved: {output_csv_path}")
    print(f"[INFO] total rows  : {len(out_df)}")
    print(f"[INFO] dropped rows: {int((out_df['frameClass'] == CLASS_DROPPED).sum())}")
    print(f"[INFO] safe normal : {int((out_df['frameClass'] == CLASS_SAFE_NORMAL).sum())}")
    print(f"[INFO] late normal : {int((out_df['frameClass'] == CLASS_LATE_NORMAL).sum())}")

    return out_df


def load_extracted_csv(csv_path: str, late_threshold_ms: float) -> pd.DataFrame:
    df = pd.read_csv(csv_path, skipinitialspace=True)
    df.columns = df.columns.str.strip()

    required_cols = [
        "frameId",
        "receiveTimeMs",
        "frameBufferInsertTimeMs",
        "frameBufferExtractTimeMs",
        "now",
        "render_time",
        "max_wait",
        "isDropped",
    ]

    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns in extracted CSV: {missing}")

    for col in required_cols:
        df[col] = to_numeric_clean(df[col])

    df["isDropped"] = df["isDropped"].fillna(0).astype(int)

    if "frameClass" not in df.columns:
        df["frameClass"] = classify_frame_type(df, late_threshold_ms)
    else:
        df["frameClass"] = df["frameClass"].astype(str)

        # 혹시 기존 CSV에 잘못된 frameClass가 있으면 재계산
        invalid_class = ~df["frameClass"].isin(CLASS_ORDER)
        if invalid_class.any():
            df["frameClass"] = classify_frame_type(df, late_threshold_ms)

    df = df.dropna(
        subset=[
            "frameId",
            "receiveTimeMs",
            "frameBufferInsertTimeMs",
            "now",
            "render_time",
            "max_wait",
        ]
    )

    df = df.sort_values("frameId").reset_index(drop=True)

    return df


def add_interval_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # receive -> frame buffer insert
    df["insertMinusReceiveMs"] = df["frameBufferInsertTimeMs"] - df["receiveTimeMs"]

    # frame buffer insert -> now
    df["nowMinusInsertMs"] = df["now"] - df["frameBufferInsertTimeMs"]

    # now -> render_time
    df["renderMinusNowMs"] = df["render_time"] - df["now"]

    # frame buffer insert -> render_time
    df["renderMinusInsertMs"] = df["render_time"] - df["frameBufferInsertTimeMs"]

    # frame buffer insert -> frame buffer extract
    valid_extract = df["frameBufferExtractTimeMs"].notna() & (df["frameBufferExtractTimeMs"] > 0)
    df["extractMinusInsertMs"] = np.nan
    df.loc[valid_extract, "extractMinusInsertMs"] = (
        df.loc[valid_extract, "frameBufferExtractTimeMs"]
        - df.loc[valid_extract, "frameBufferInsertTimeMs"]
    )

    return df


def make_stats_dict(series: pd.Series) -> dict:
    s = series.dropna()

    if len(s) == 0:
        return {
            "count": 0,
            "mean": np.nan,
            "min": np.nan,
            "max": np.nan,
            "std": np.nan,
        }

    return {
        "count": int(len(s)),
        "mean": float(s.mean()),
        "min": float(s.min()),
        "max": float(s.max()),
        "std": float(s.std()),
    }


def format_stat_value(value) -> str:
    if pd.isna(value):
        return "NaN"
    return f"{value:.2f}"


def build_stats_text(df: pd.DataFrame, col: str) -> str:
    all_stats = make_stats_dict(df[col])

    lines = [
        f"ALL n={all_stats['count']}",
        (
            f"  mean={format_stat_value(all_stats['mean'])}, "
            f"min={format_stat_value(all_stats['min'])}, "
            f"max={format_stat_value(all_stats['max'])}, "
            f"std={format_stat_value(all_stats['std'])}"
        ),
    ]

    for cls in CLASS_ORDER:
        cls_stats = make_stats_dict(df.loc[df["frameClass"] == cls, col])
        label = CLASS_LABEL[cls].upper()

        lines.append(f"{label} n={cls_stats['count']}")
        lines.append(
            f"  mean={format_stat_value(cls_stats['mean'])}, "
            f"min={format_stat_value(cls_stats['min'])}, "
            f"max={format_stat_value(cls_stats['max'])}, "
            f"std={format_stat_value(cls_stats['std'])}"
        )

    return "\n".join(lines)


def add_stats_box(ax, df: pd.DataFrame, col: str) -> None:
    text = build_stats_text(df, col)

    ax.text(
        0.995,
        0.97,
        text,
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=7.5,
        family="monospace",
        bbox={
            "boxstyle": "round,pad=0.35",
            "facecolor": "white",
            "edgecolor": "gray",
            "alpha": 0.82,
        },
    )


def save_interval_stats_txt(df: pd.DataFrame, stats_path: str) -> None:
    cols = [
        "insertMinusReceiveMs",
        "nowMinusInsertMs",
        "renderMinusNowMs",
        "renderMinusInsertMs",
        "extractMinusInsertMs",
        "max_wait",
    ]

    os.makedirs(os.path.dirname(stats_path), exist_ok=True)

    with open(stats_path, "w", encoding="utf-8") as f:
        f.write("Timing interval statistics by frame class\n")
        f.write("=========================================\n\n")

        f.write("[CLASS COUNTS]\n")
        f.write(f"total       : {len(df)}\n")
        for cls in CLASS_ORDER:
            f.write(f"{cls:<12}: {int((df['frameClass'] == cls).sum())}\n")
        f.write("\n")

        for col in cols:
            f.write(f"[{col}]\n")
            f.write(build_stats_text(df, col))
            f.write("\n\n")

    print(f"[INFO] stats txt saved: {stats_path}")


def print_interval_summary(df: pd.DataFrame) -> None:
    print("\n[INFO] frame class counts")
    print(df["frameClass"].value_counts().reindex(CLASS_ORDER, fill_value=0))

    print("\n[INFO] interval summary")

    cols = [
        "insertMinusReceiveMs",
        "nowMinusInsertMs",
        "renderMinusNowMs",
        "renderMinusInsertMs",
        "extractMinusInsertMs",
        "max_wait",
    ]

    for col in cols:
        print(f"\n[{col}] ALL")
        print(df[col].describe())

        for cls in CLASS_ORDER:
            print(f"\n[{col}] {cls}")
            print(df.loc[df["frameClass"] == cls, col].describe())


def plot_one_interval(ax, df: pd.DataFrame, y_col: str, title: str, ylabel: str) -> None:
    x = np.arange(len(df))

    for cls in CLASS_ORDER:
        mask = (df["frameClass"] == cls) & df[y_col].notna()

        if mask.sum() == 0:
            continue

        ax.scatter(
            x[mask],
            df.loc[mask, y_col],
            color=CLASS_COLOR[cls],
            marker=CLASS_MARKER[cls],
            s=24 if cls != CLASS_DROPPED else 32,
            linewidths=1.2,
            alpha=0.82,
            label=CLASS_LABEL[cls],
            zorder=5 if cls != CLASS_SAFE_NORMAL else 4,
        )

    ax.axhline(0, color="black", linewidth=1.0, alpha=0.45)
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left")

    add_stats_box(ax, df, y_col)


def plot_interval_metrics(df: pd.DataFrame, out_path: str) -> None:
    fig, axes = plt.subplots(
        5,
        1,
        figsize=(18, 18),
        sharex=True,
        gridspec_kw={"height_ratios": [2, 2, 2, 2, 2]},
    )

    plot_one_interval(
        axes[0],
        df,
        "insertMinusReceiveMs",
        "receiveTimeMs → frameBufferInsertTimeMs",
        "ms",
    )

    plot_one_interval(
        axes[1],
        df,
        "nowMinusInsertMs",
        "frameBufferInsertTimeMs → now",
        "ms",
    )

    plot_one_interval(
        axes[2],
        df,
        "renderMinusNowMs",
        "now → render_time",
        "ms",
    )

    plot_one_interval(
        axes[3],
        df,
        "renderMinusInsertMs",
        "frameBufferInsertTimeMs → render_time",
        "ms",
    )

    plot_one_interval(
        axes[4],
        df,
        "extractMinusInsertMs",
        "frameBufferInsertTimeMs → frameBufferExtractTimeMs",
        "ms",
    )

    axes[-1].set_xlabel("Frame index")

    plt.tight_layout()
    plt.savefig(out_path, dpi=200)
    plt.close()

    print(f"[INFO] interval plot saved: {out_path}")


def select_sample_frames(
    df: pd.DataFrame,
    sample_mode: str,
    sample_count: int,
    sample_start: int,
) -> pd.DataFrame:
    n = len(df)
    if n == 0:
        return df.copy()

    sample_count = max(1, min(sample_count, n))

    if sample_mode == "first":
        return df.iloc[:sample_count].copy()

    if sample_mode == "even":
        indices = np.linspace(0, n - 1, sample_count).round().astype(int)
        return df.iloc[indices].copy()

    if sample_mode == "start":
        start = max(0, min(sample_start, n - 1))
        end = min(start + sample_count, n)
        return df.iloc[start:end].copy()

    if sample_mode == "drop_context":
        dropped_indices = df.index[df["frameClass"] == CLASS_DROPPED].tolist()

        if not dropped_indices:
            return df.iloc[:sample_count].copy()

        center = dropped_indices[0]
        half = sample_count // 2
        start = max(0, center - half)
        end = min(n, start + sample_count)
        start = max(0, end - sample_count)

        return df.iloc[start:end].copy()

    if sample_mode == "late_context":
        late_indices = df.index[df["frameClass"] == CLASS_LATE_NORMAL].tolist()

        if not late_indices:
            return df.iloc[:sample_count].copy()

        center = late_indices[0]
        half = sample_count // 2
        start = max(0, center - half)
        end = min(n, start + sample_count)
        start = max(0, end - sample_count)

        return df.iloc[start:end].copy()

    raise ValueError(f"Unknown sample_mode: {sample_mode}")


def plot_timeline_sample(
    df: pd.DataFrame,
    out_path: str,
    sample_mode: str,
    sample_count: int,
    sample_start: int,
) -> None:
    sample = select_sample_frames(
        df=df,
        sample_mode=sample_mode,
        sample_count=sample_count,
        sample_start=sample_start,
    ).reset_index(drop=True)

    if len(sample) == 0:
        print("[WARN] no sample rows for timeline plot")
        return

    time_cols = [
        "receiveTimeMs",
        "frameBufferInsertTimeMs",
        "now",
        "render_time",
    ]

    # 절대 시간축은 유지하되 sample 내부 최소 시간을 base로 뺀다.
    # 프레임 간 시간 간격은 그대로 유지된다.
    base_time = sample[time_cols].min().min()

    sample["receive_rel"] = sample["receiveTimeMs"] - base_time
    sample["fb_insert_rel"] = sample["frameBufferInsertTimeMs"] - base_time
    sample["now_rel"] = sample["now"] - base_time
    sample["render_rel"] = sample["render_time"] - base_time

    rel_cols = [
        "receive_rel",
        "fb_insert_rel",
        "now_rel",
        "render_rel",
    ]

    rel_values = sample[rel_cols].replace([np.inf, -np.inf], np.nan).values.flatten()
    rel_values = rel_values[~np.isnan(rel_values)]

    if len(rel_values) == 0:
        print("[WARN] no valid timing values for timeline plot")
        return

    x_min = np.nanmin(rel_values) - 10
    x_max = np.nanmax(rel_values) + 10

    y = np.arange(len(sample))

    fig_height = max(6, 0.45 * len(sample) + 3)
    fig, ax = plt.subplots(figsize=(18, fig_height))

    event_specs = [
        ("receive_rel", "receive", "tab:blue", "o"),
        ("fb_insert_rel", "fb_insert", "tab:orange", "s"),
        ("now_rel", "now", "tab:green", "^"),
        ("render_rel", "render_time", "tab:purple", "D"),
    ]

    label_used = set()

    for i, row in sample.iterrows():
        cls = row["frameClass"]
        cls_color = CLASS_COLOR.get(cls, "gray")
        cls_label = CLASS_LABEL.get(cls, cls)
        is_dropped = cls == CLASS_DROPPED
        is_late_normal = cls == CLASS_LATE_NORMAL

        # frame class별 row 배경
        if is_dropped:
            ax.axhspan(i - 0.42, i + 0.42, color="tab:red", alpha=0.14)
        elif is_late_normal:
            ax.axhspan(i - 0.42, i + 0.42, color="tab:orange", alpha=0.14)

        row_times = [
            row["receive_rel"],
            row["fb_insert_rel"],
            row["now_rel"],
            row["render_rel"],
        ]

        row_min = np.nanmin(row_times)
        row_max = np.nanmax(row_times)

        # 한 frame 내부 전체 시간 범위
        ax.hlines(
            y=i,
            xmin=row_min,
            xmax=row_max,
            color=cls_color,
            linewidth=1.2,
            alpha=0.55,
        )

        # receive -> frameBufferInsert 구간
        ax.hlines(
            y=i,
            xmin=row["receive_rel"],
            xmax=row["fb_insert_rel"],
            color="tab:blue",
            linewidth=3.0,
            alpha=0.55,
        )

        # frameBufferInsert -> now 구간
        ax.hlines(
            y=i,
            xmin=row["fb_insert_rel"],
            xmax=row["now_rel"],
            color="tab:orange",
            linewidth=3.0,
            alpha=0.45,
        )

        # 각 timestamp marker
        for event_col, event_label, event_color, marker in event_specs:
            label = event_label if event_label not in label_used else None

            ax.scatter(
                row[event_col],
                i,
                s=45,
                color=event_color,
                marker=marker,
                label=label,
                zorder=5,
            )

            label_used.add(event_label)

        # frame class marker를 now 위치에 추가
        class_legend_key = f"class:{cls}"

        if cls == CLASS_SAFE_NORMAL:
            marker = "o"
            size = 65
        elif cls == CLASS_LATE_NORMAL:
            marker = "P"
            size = 90
        else:
            marker = "x"
            size = 90

        label = cls_label if class_legend_key not in label_used else None

        ax.scatter(
            row["now_rel"],
            i,
            color=cls_color,
            marker=marker,
            s=size,
            linewidths=1.8,
            label=label,
            zorder=7,
        )

        label_used.add(class_legend_key)

        # 각 frame 옆에 핵심 간격 텍스트 표시
        insert_minus_receive = row["frameBufferInsertTimeMs"] - row["receiveTimeMs"]
        now_minus_insert = row["now"] - row["frameBufferInsertTimeMs"]
        render_minus_now = row["render_time"] - row["now"]
        max_wait = row["max_wait"]

        text = (
            f"{cls_label}, "
            f"recv→ins={insert_minus_receive:.0f}ms, "
            f"ins→now={now_minus_insert:.0f}ms, "
            f"render-now={render_minus_now:.0f}ms, "
            f"max_wait={max_wait:.0f}ms"
        )

        ax.text(
            x_max,
            i,
            text,
            va="center",
            ha="left",
            fontsize=8,
            family="monospace",
        )

    ytick_labels = []
    for _, row in sample.iterrows():
        cls = row["frameClass"]

        if cls == CLASS_SAFE_NORMAL:
            prefix = "S"
        elif cls == CLASS_LATE_NORMAL:
            prefix = "L"
        else:
            prefix = "D"

        ytick_labels.append(f"{prefix} {int(row['frameId'])}")

    ax.set_yticks(y)
    ax.set_yticklabels(ytick_labels, fontsize=8)

    ax.invert_yaxis()

    ax.set_xlim(x_min, x_max + 430)

    ax.set_xlabel(f"Relative absolute time from sample start (ms), base={int(base_time)}")
    ax.set_ylabel("Frame ID")
    ax.set_title(
        f"Absolute timing timeline: SAFE_NORMAL / LATE_NORMAL / DROPPED "
        f"({sample_mode}, n={len(sample)})"
    )

    ax.grid(True, axis="x", alpha=0.3)
    ax.legend(loc="best")

    plt.tight_layout()
    plt.savefig(out_path, dpi=200)
    plt.close()

    print(f"[INFO] absolute timeline plot saved: {out_path}")


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Extract timing columns and plot interval metrics plus frame timeline "
            "with 3-class frame classification."
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
        help="Optional custom input frame_records CSV path",
    )

    parser.add_argument(
        "--extracted-output",
        type=str,
        default=None,
        help="Optional custom extracted timing CSV output path",
    )

    parser.add_argument(
        "--interval-plot",
        type=str,
        default=None,
        help="Optional custom interval plot output path",
    )

    parser.add_argument(
        "--timeline-plot",
        type=str,
        default=None,
        help="Optional custom timeline plot output path",
    )

    parser.add_argument(
        "--stats-output",
        type=str,
        default=None,
        help="Optional custom stats txt output path",
    )

    parser.add_argument(
        "--sample-mode",
        type=str,
        default="drop_context",
        choices=["first", "even", "drop_context", "late_context", "start"],
        help="Timeline sample mode. Default: drop_context",
    )

    parser.add_argument(
        "--sample-count",
        type=int,
        default=20,
        help="Number of frames to show in timeline plot. Default: 20",
    )

    parser.add_argument(
        "--sample-start",
        type=int,
        default=0,
        help="Start frame index when --sample-mode start is used. Default: 0",
    )

    parser.add_argument(
        "--late-threshold-ms",
        type=float,
        default=DEFAULT_LATE_THRESHOLD_MS,
        help="Threshold for late normal classification. Default: -5 ms",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    if args.input is not None:
        input_csv_path = args.input
    else:
        if args.pacing == 1:
            input_csv_path = "/home/n2sl/yeon/qos/network/log/frame/frame_records_pacing.csv"
        else:
            input_csv_path = "/home/n2sl/yeon/qos/network/log/frame/frame_records.csv"

    if args.extracted_output is not None:
        extracted_csv_path = args.extracted_output
    else:
        if args.pacing == 1:
            extracted_csv_path = "/home/n2sl/yeon/qos/network/log/frame/plots/timing_3class_pacing.csv"
        else:
            extracted_csv_path = "/home/n2sl/yeon/qos/network/log/frame/plots/timing_3class.csv"

    if args.interval_plot is not None:
        interval_plot_path = args.interval_plot
    else:
        if args.pacing == 1:
            interval_plot_path = "/home/n2sl/yeon/qos/network/log/frame/plots/timing_intervals_3class_pacing.png"
        else:
            interval_plot_path = "/home/n2sl/yeon/qos/network/log/frame/plots/timing_intervals_3class.png"

    if args.timeline_plot is not None:
        timeline_plot_path = args.timeline_plot
    else:
        if args.pacing == 1:
            timeline_plot_path = "/home/n2sl/yeon/qos/network/log/frame/plots/timing_timeline_3class_sample_pacing.png"
        else:
            timeline_plot_path = "/home/n2sl/yeon/qos/network/log/frame/plots/timing_timeline_3class_sample.png"

    if args.stats_output is not None:
        stats_output_path = args.stats_output
    else:
        if args.pacing == 1:
            stats_output_path = "/home/n2sl/yeon/qos/network/log/frame/plots/timing_interval_stats_3class_pacing.txt"
        else:
            stats_output_path = "/home/n2sl/yeon/qos/network/log/frame/plots/timing_interval_stats_3class.txt"

    os.makedirs(os.path.dirname(extracted_csv_path), exist_ok=True)
    os.makedirs(os.path.dirname(interval_plot_path), exist_ok=True)
    os.makedirs(os.path.dirname(timeline_plot_path), exist_ok=True)
    os.makedirs(os.path.dirname(stats_output_path), exist_ok=True)

    print(f"[INFO] input csv         : {input_csv_path}")
    print(f"[INFO] extracted csv     : {extracted_csv_path}")
    print(f"[INFO] interval plot     : {interval_plot_path}")
    print(f"[INFO] timeline plot     : {timeline_plot_path}")
    print(f"[INFO] stats output      : {stats_output_path}")
    print(f"[INFO] late threshold ms : {args.late_threshold_ms}")

    extract_timing_drop_csv(
        input_csv_path=input_csv_path,
        output_csv_path=extracted_csv_path,
        late_threshold_ms=args.late_threshold_ms,
    )

    df = load_extracted_csv(
        csv_path=extracted_csv_path,
        late_threshold_ms=args.late_threshold_ms,
    )

    df = add_interval_columns(df)

    print_interval_summary(df)
    save_interval_stats_txt(df, stats_output_path)
    plot_interval_metrics(df, interval_plot_path)

    plot_timeline_sample(
        df=df,
        out_path=timeline_plot_path,
        sample_mode=args.sample_mode,
        sample_count=args.sample_count,
        sample_start=args.sample_start,
    )


if __name__ == "__main__":
    main()