import os
import argparse
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


INVALID_BIG_THRESHOLD = 1e18

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


def to_numeric_clean(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    s = s.mask(np.isinf(s), np.nan)
    s = s.mask(s.abs() > INVALID_BIG_THRESHOLD, np.nan)
    return s


def classify_dropped_frame(df: pd.DataFrame) -> pd.Series:
    """
    드랍 프레임 판별 규칙.
    decodeStartMs, decodeFinishMs, frameBufferExtractTimeMs 중 하나라도 invalid면 dropped 처리.
    """
    decode_start_invalid = df["decodeStartMs"].isna() | (df["decodeStartMs"] <= 0)
    decode_finish_invalid = df["decodeFinishMs"].isna() | (df["decodeFinishMs"] <= 0)
    fb_extract_invalid = df["frameBufferExtractTimeMs"].isna() | (df["frameBufferExtractTimeMs"] <= 0)

    return decode_start_invalid | decode_finish_invalid | fb_extract_invalid


def classify_frame_class(df: pd.DataFrame, late_threshold_ms: float) -> pd.Series:
    """
    3가지 frame class 분류.

    SAFE_NORMAL:
        isDropped == False and max_wait > late_threshold_ms

    LATE_NORMAL:
        isDropped == False and max_wait <= late_threshold_ms

    DROPPED:
        isDropped == True
    """
    frame_class = pd.Series(CLASS_SAFE_NORMAL, index=df.index, dtype="object")

    dropped_mask = df["isDropped"] == True
    late_normal_mask = (df["isDropped"] == False) & (df["max_wait"] <= late_threshold_ms)

    frame_class.loc[late_normal_mask] = CLASS_LATE_NORMAL
    frame_class.loc[dropped_mask] = CLASS_DROPPED

    return frame_class


def load_and_prepare_csv(csv_path: str, late_threshold_ms: float) -> pd.DataFrame:
    df = pd.read_csv(csv_path, skipinitialspace=True)
    df.columns = df.columns.str.strip()

    required_cols = [
        "frameId",
        "receiveTimeMs",
        "frameBufferInsertTimeMs",
        "frameBufferExtractTimeMs",

        "decodeStartMs",
        "decodeFinishMs",
        "decodeSlackNominalMs",
        "decodeSlackEffectiveMs",
        "decodeQueueResidenceMs",
        "frameBufferResidenceMs",
        "currentSpatialLayer",
        "availableBitrateBps",

        "now",
        "render_time",
        "max_wait",
    ]

    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns in CSV: {missing}")

    for col in required_cols:
        df[col] = to_numeric_clean(df[col])

    df["queueResidenceMs"] = df["decodeQueueResidenceMs"] + df["frameBufferResidenceMs"]
    df["preDecodeMs"] = df["frameBufferInsertTimeMs"] - df["receiveTimeMs"]
    df["renderMinusNowMs"] = df["render_time"] - df["now"]
    df["renderMinusInsertMs"] = df["render_time"] - df["frameBufferInsertTimeMs"]
    df["nowMinusInsertMs"] = df["now"] - df["frameBufferInsertTimeMs"]

    df["isDropped"] = classify_dropped_frame(df)
    df["frameClass"] = classify_frame_class(df, late_threshold_ms)

    df = df.dropna(
        subset=[
            "frameId",
            "max_wait",
        ]
    ).copy()



    df = df.sort_values("frameId").reset_index(drop=True)

    return df


def describe_series(series: pd.Series) -> dict:
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


def fmt_value(v) -> str:
    if pd.isna(v):
        return "NaN"
    return f"{v:.6f}"


def write_stats_block(f, title: str, stats: dict, unit: str = "ms") -> None:
    f.write(f"[{title}]\n")
    f.write(f"count: {stats['count']}\n")
    f.write(f"mean : {fmt_value(stats['mean'])} {unit}\n")
    f.write(f"min  : {fmt_value(stats['min'])} {unit}\n")
    f.write(f"max  : {fmt_value(stats['max'])} {unit}\n")
    f.write(f"std  : {fmt_value(stats['std'])} {unit}\n")
    f.write("\n")


def save_class_stats(df: pd.DataFrame, txt_path: str, late_threshold_ms: float) -> None:
    metrics = [
        "decodeSlackEffectiveMs",
        "queueResidenceMs",
        "decodeSlackNominalMs",
        "preDecodeMs",
        "renderMinusNowMs",
        "renderMinusInsertMs",
        "nowMinusInsertMs",
        "max_wait",
    ]

    os.makedirs(os.path.dirname(txt_path), exist_ok=True)

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("Frame timing statistics by 3 classes\n")
        f.write("====================================\n\n")

        f.write("[CLASS DEFINITION]\n")
        f.write(f"{CLASS_SAFE_NORMAL}: isDropped == False and max_wait > {late_threshold_ms}\n")
        f.write(f"{CLASS_LATE_NORMAL}: isDropped == False and max_wait <= {late_threshold_ms}\n")
        f.write(f"{CLASS_DROPPED}: isDropped == True\n\n")

        f.write("[COUNTS]\n")
        f.write(f"total rows     : {len(df)}\n")
        for cls in CLASS_ORDER:
            f.write(f"{cls:<13}: {int((df['frameClass'] == cls).sum())}\n")
        f.write("\n")

        for metric in metrics:
            if metric not in df.columns:
                continue

            unit = "Mbps" if metric == "availableBitrateMbps" else "ms"

            f.write(f"========== {metric} ==========\n\n")
            write_stats_block(f, "ALL FRAMES", describe_series(df[metric]), unit=unit)

            for cls in CLASS_ORDER:
                write_stats_block(
                    f,
                    cls,
                    describe_series(df.loc[df["frameClass"] == cls, metric]),
                    unit=unit,
                )

    print(f"[INFO] Saved class stats: {txt_path}")


def print_summary(df: pd.DataFrame) -> None:
    print(f"\n[INFO] total rows: {len(df)}")

    print("\n[INFO] frame class counts:")
    print(df["frameClass"].value_counts().reindex(CLASS_ORDER, fill_value=0))

    print("\n[INFO] currentSpatialLayer counts:")
    print(df["currentSpatialLayer"].value_counts().sort_index())

    print("\n[INFO] main metric summary by class:")
    metrics = [
        "decodeSlackEffectiveMs",
        "queueResidenceMs",
        "decodeSlackNominalMs",
        "preDecodeMs",
        "max_wait",
    ]

    for metric in metrics:
        print(f"\n========== {metric} ==========")
        for cls in CLASS_ORDER:
            print(f"\n[{cls}]")
            print(df.loc[df["frameClass"] == cls, metric].describe())


def get_class_means_counts_stds(df: pd.DataFrame, metric: str):
    means = []
    counts = []
    stds = []

    for cls in CLASS_ORDER:
        s = df.loc[df["frameClass"] == cls, metric].dropna()
        counts.append(len(s))

        if len(s) == 0:
            means.append(np.nan)
            stds.append(np.nan)
        else:
            means.append(float(s.mean()))
            stds.append(float(s.std()))

    return means, counts, stds


def annotate_bar(ax, bar, value, count) -> None:
    if pd.isna(value):
        text = f"NaN\nn={count}"
        y = 0.0
        va = "bottom"
    else:
        text = f"{value:.2f}\nn={count}"

        if value >= 0:
            y = value
            va = "bottom"
        else:
            y = value
            va = "top"

    x = bar.get_x() + bar.get_width() / 2.0

    ax.text(
        x,
        y,
        text,
        ha="center",
        va=va,
        fontsize=8,
        family="monospace",
    )


def plot_single_metric_mean_bar(
    ax,
    df: pd.DataFrame,
    metric: str,
    title: str,
    y_label: str,
    late_threshold_ms: float = None,
) -> None:
    means, counts, _ = get_class_means_counts_stds(df, metric)

    x = np.arange(len(CLASS_ORDER))
    colors = [CLASS_COLOR[cls] for cls in CLASS_ORDER]
    labels = [CLASS_LABEL[cls] for cls in CLASS_ORDER]

    bars = ax.bar(
        x,
        means,
        color=colors,
        alpha=0.85,
        edgecolor="black",
        linewidth=0.8,
    )

    for bar, value, count in zip(bars, means, counts):
        annotate_bar(ax, bar, value, count)

    ax.axhline(0, color="black", linewidth=1.0, alpha=0.5)

    if late_threshold_ms is not None:
        ax.axhline(
            late_threshold_ms,
            color="tab:orange",
            linewidth=1.0,
            linestyle="--",
            alpha=0.85,
        )

        ax.text(
            0.02,
            0.06,
            f"late threshold = {late_threshold_ms} ms",
            transform=ax.transAxes,
            fontsize=9,
            color="tab:orange",
            bbox={
                "boxstyle": "round,pad=0.25",
                "facecolor": "white",
                "edgecolor": "tab:orange",
                "alpha": 0.75,
            },
        )

    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel(y_label)
    ax.set_title(title)
    ax.grid(True, axis="y", alpha=0.3)


def plot_grouped_metric_mean_bar(
    ax,
    df: pd.DataFrame,
    metrics,
    metric_labels,
    title: str,
    y_label: str,
) -> None:
    x = np.arange(len(CLASS_ORDER))

    if len(metrics) == 1:
        width = 0.55
    else:
        width = 0.32

    offsets = np.linspace(
        -width * (len(metrics) - 1) / 2.0,
        width * (len(metrics) - 1) / 2.0,
        len(metrics),
    )

    for metric, metric_label, offset in zip(metrics, metric_labels, offsets):
        means, counts, _ = get_class_means_counts_stds(df, metric)

        bars = ax.bar(
            x + offset,
            means,
            width=width,
            alpha=0.82,
            edgecolor="black",
            linewidth=0.8,
            label=metric_label,
        )

        for bar, value, count in zip(bars, means, counts):
            annotate_bar(ax, bar, value, count)

    ax.axhline(0, color="black", linewidth=1.0, alpha=0.5)
    ax.set_xticks(x)
    ax.set_xticklabels([CLASS_LABEL[cls] for cls in CLASS_ORDER])
    ax.set_ylabel(y_label)
    ax.set_title(title)
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend(loc="best")


def plot_all_metrics_as_bars(
    df: pd.DataFrame,
    out_path: str,
    late_threshold_ms: float,
) -> None:
    fig, axes = plt.subplots(
        4,
        1,
        figsize=(15, 15),
        sharex=False,
        gridspec_kw={"height_ratios": [2.4, 2.0, 2.0, 2.0]},
    )

    ax1, ax2, ax3, ax4 = axes

    # 1. decodeSlackEffectiveMs / queueResidenceMs
    plot_grouped_metric_mean_bar(
        ax=ax1,
        df=df,
        metrics=["decodeSlackEffectiveMs", "queueResidenceMs"],
        metric_labels=["decodeSlackEffectiveMs", "queueResidenceMs"],
        title="Mean decodeSlackEffectiveMs / queueResidenceMs by 3 classes",
        y_label="Mean milliseconds (ms)",
    )

    # 2. decodeSlackNominalMs
    plot_single_metric_mean_bar(
        ax=ax2,
        df=df,
        metric="decodeSlackNominalMs",
        title="Mean decodeSlackNominalMs by 3 classes",
        y_label="Mean milliseconds (ms)",
    )

    # 3. preDecodeMs
    plot_single_metric_mean_bar(
        ax=ax3,
        df=df,
        metric="preDecodeMs",
        title="Mean preDecodeMs (= frameBufferInsertTimeMs - receiveTimeMs) by 3 classes",
        y_label="Mean milliseconds (ms)",
    )

    # 4. max_wait
    plot_single_metric_mean_bar(
        ax=ax4,
        df=df,
        metric="max_wait",
        title="Mean max_wait by 3 classes",
        y_label="Mean milliseconds (ms)",
        late_threshold_ms=late_threshold_ms,
    )

    ax4.set_xlabel("Frame class")

    plt.tight_layout()
    plt.savefig(out_path, dpi=200)
    plt.close()

    print(f"[INFO] Saved bar plot: {out_path}")


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Plot only bar charts for all metrics by 3-class frame classification."
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
        help="Optional custom input CSV path",
    )

    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Optional custom plot output path",
    )

    parser.add_argument(
        "--stats-output",
        type=str,
        default=None,
        help="Optional custom stats txt output path",
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
            csv_path = "/home/n2sl/yeon/qos/network/log/frame/frame_records_pacing.csv"
        else:
            csv_path = "/home/n2sl/yeon/qos/network/log/frame/frame_records.csv"

    if args.output is not None:
        out_path = args.output
    else:
        if args.pacing == 1:
            out_path = "/home/n2sl/yeon/qos/network/log/frame/plots/effective_metrics_3class_baronly_pacing.png"
        else:
            out_path = "/home/n2sl/yeon/qos/network/log/frame/plots/effective_metrics_3class_baronly.png"

    if args.stats_output is not None:
        txt_path = args.stats_output
    else:
        if args.pacing == 1:
            txt_path = "/home/n2sl/yeon/qos/network/log/frame/plots/effective_metrics_3class_baronly_stats_pacing.txt"
        else:
            txt_path = "/home/n2sl/yeon/qos/network/log/frame/plots/effective_metrics_3class_baronly_stats.txt"

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    os.makedirs(os.path.dirname(txt_path), exist_ok=True)

    print(f"[INFO] csv_path          : {csv_path}")
    print(f"[INFO] out_path          : {out_path}")
    print(f"[INFO] txt_path          : {txt_path}")
    print(f"[INFO] late_threshold_ms : {args.late_threshold_ms}")

    df = load_and_prepare_csv(
        csv_path=csv_path,
        late_threshold_ms=args.late_threshold_ms,
    )

    print(f"[INFO] loaded rows: {len(df)}")
    print_summary(df)

    save_class_stats(
        df=df,
        txt_path=txt_path,
        late_threshold_ms=args.late_threshold_ms,
    )

    plot_all_metrics_as_bars(
        df=df,
        out_path=out_path,
        late_threshold_ms=args.late_threshold_ms,
    )


if __name__ == "__main__":
    main()