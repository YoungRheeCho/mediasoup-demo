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


def to_numeric_clean(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    s = s.mask(np.isinf(s), np.nan)
    s = s.mask(s.abs() > INVALID_BIG_THRESHOLD, np.nan)
    return s


def classify_dropped_frame(df: pd.DataFrame) -> pd.Series:
    """
    dropped frame 판정.

    정상 decode된 frame이라면 최소한 아래 단계들이 있어야 함.

    receiveTimeMs
    frameBufferInsertTimeMs
    frameBufferExtractTimeMs
    decodeQueueInsertTimeMs
    decodeQueueExtractTimeMs
    decodeStartMs
    decodeFinishMs

    이 중 핵심 단계가 0/NaN이면 dropped로 본다.
    """

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
        if col in df.columns:
            dropped = dropped | df[col].isna() | (df[col] <= 0)

    return dropped


def classify_frame_class(df: pd.DataFrame, late_threshold_ms: float) -> pd.Series:
    """
    3-class 분류.

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
        "render_time",
        "frameBufferInsertTimeMs",
        "frameBufferExtractTimeMs",

        "decodeSlackNominalMs",
        "decodeQueueResidenceMs",
        "frameBufferResidenceMs",

        "currentSpatialLayer",
        "availableBitrateBps",
        "gccAvailableBitrateBps",
        "camelAvailableBitrateBps",

        # pacing / ACE metrics
        "aceQueueBytes",
        "pacingBacklogBytes",
        "pacingBucketSizeBytes",
        "camelBurstLengthBytes",

        "lossRate",

        # 3-class 분류용
        "decodeStartMs",
        "decodeFinishMs",
        "max_wait",
    ]

    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns in CSV: {missing}")

    for col in required_cols:
        df[col] = to_numeric_clean(df[col])

    # render_time - receiveTimeMs
    # 양수: receive가 render_time보다 빠름
    # 음수: receive가 render_time보다 늦음
    df["renderMinusReceiveMs"] = np.nan

    valid_render_receive = (
        df["render_time"].notna()
        & (df["render_time"] > 0)
        & df["receiveTimeMs"].notna()
        & (df["receiveTimeMs"] > 0)
    )

    df.loc[valid_render_receive, "renderMinusReceiveMs"] = (
        df.loc[valid_render_receive, "render_time"]
        - df.loc[valid_render_receive, "receiveTimeMs"]
    )

    # pre-decoding 지표
    # preDecodeWaitingMs = frameBufferInsertTimeMs - receiveTimeMs
    #
    # frameBufferInsertTimeMs <= 0 또는 NaN이면:
    #   frame buffer insert까지 못 간 프레임으로 보고
    #   그래프 표시용으로 preDecodeWaitingMs = 0 으로 찍음
    df["preDecodeWaitingMs"] = np.nan

    valid_receive = (
        df["receiveTimeMs"].notna()
        & (df["receiveTimeMs"] > 0)
    )

    valid_fb_insert = (
        df["frameBufferInsertTimeMs"].notna()
        & (df["frameBufferInsertTimeMs"] > 0)
    )

    # 정상적으로 frameBufferInsert까지 간 프레임
    valid_predecode_mask = valid_receive & valid_fb_insert

    df.loc[valid_predecode_mask, "preDecodeWaitingMs"] = (
        df.loc[valid_predecode_mask, "frameBufferInsertTimeMs"]
        - df.loc[valid_predecode_mask, "receiveTimeMs"]
    )

    # frameBufferInsert까지 못 간 프레임
    # 그래프에서는 0ms 위치에 표시
    no_frame_buffer_insert_mask = valid_receive & (~valid_fb_insert)

    df.loc[no_frame_buffer_insert_mask, "preDecodeWaitingMs"] = 0.0

    # 방어 코드:
    # 실제 timestamp 순서가 이상해서 음수가 나온 경우도 0으로 정리
    df.loc[df["preDecodeWaitingMs"] < 0, "preDecodeWaitingMs"] = 0.0

    # queue residence
    df["queueResidenceMs"] = (
        df["decodeQueueResidenceMs"] + df["frameBufferResidenceMs"]
    )

    # bitrate
    df["availableBitrateMbps"] = df["availableBitrateBps"] / 1_000_000.0
    df["gccAvailableBitrateMbps"] = df["gccAvailableBitrateBps"] / 1_000_000.0
    df["camelAvailableBitrateMbps"] = df["camelAvailableBitrateBps"] / 1_000_000.0

    # 3-class 분류
    df["isDropped"] = classify_dropped_frame(df)
    df["frameClass"] = classify_frame_class(df, late_threshold_ms)

    # dropped frame에서 일부 metric이 NaN일 수 있으므로 frameId만 필수로 둠
    df = df.dropna(subset=["frameId"]).copy()

    df["currentSpatialLayer"] = pd.to_numeric(
        df["currentSpatialLayer"],
        errors="coerce",
    ).fillna(-1).astype(int)

    df = df.sort_values("frameId").reset_index(drop=True)

    return df


def add_late_drop_circles(
    ax,
    x,
    df: pd.DataFrame,
    y_col: str,
    late_label: str = "late normal",
    drop_label: str = "dropped",
) -> None:
    """
    선 그래프 위에 LATE_NORMAL / DROPPED 위치만 빈 동그라미로 표시.
    SAFE_NORMAL은 따로 표시하지 않음.
    """
    valid = df[y_col].notna()

    late_mask = valid & (df["frameClass"] == CLASS_LATE_NORMAL)
    drop_mask = valid & (df["frameClass"] == CLASS_DROPPED)

    if late_mask.sum() > 0:
        ax.scatter(
            np.asarray(x)[late_mask],
            df.loc[late_mask, y_col],
            facecolors="none",
            edgecolors="tab:orange",
            marker="o",
            s=75,
            linewidths=1.9,
            label=late_label,
            zorder=8,
        )

    if drop_mask.sum() > 0:
        ax.scatter(
            np.asarray(x)[drop_mask],
            df.loc[drop_mask, y_col],
            facecolors="none",
            edgecolors="tab:red",
            marker="o",
            s=90,
            linewidths=2.1,
            label=drop_label,
            zorder=9,
        )


def print_summary(df: pd.DataFrame) -> None:
    print(f"\n[INFO] loaded rows: {len(df)}")

    print("\n[INFO] frame class counts:")
    print(
        df["frameClass"].value_counts().reindex(
            [CLASS_SAFE_NORMAL, CLASS_LATE_NORMAL, CLASS_DROPPED],
            fill_value=0,
        )
    )

    print("\n[INFO] max_wait summary by class:")
    for cls in [CLASS_SAFE_NORMAL, CLASS_LATE_NORMAL, CLASS_DROPPED]:
        print(f"\n[{cls}]")
        print(df.loc[df["frameClass"] == cls, "max_wait"].describe())

    print("\n[INFO] preDecodeWaitingMs summary by class:")
    for cls in [CLASS_SAFE_NORMAL, CLASS_LATE_NORMAL, CLASS_DROPPED]:
        print(f"\n[{cls}]")
        print(df.loc[df["frameClass"] == cls, "preDecodeWaitingMs"].describe())

    print("\n[INFO] renderMinusReceiveMs summary by class:")
    for cls in [CLASS_SAFE_NORMAL, CLASS_LATE_NORMAL, CLASS_DROPPED]:
        print(f"\n[{cls}]")
        print(df.loc[df["frameClass"] == cls, "renderMinusReceiveMs"].describe())

    print("\n[INFO] ACE / pacing metrics summary:")
    pacing_metric_cols = [
        "aceQueueBytes",
        "pacingBacklogBytes",
        "pacingBucketSizeBytes",
        "camelBurstLengthBytes",
    ]
    print(df[pacing_metric_cols].describe())


def plot_metrics(
    df: pd.DataFrame,
    out_path: str,
    late_threshold_ms: float,
) -> None:
    x = np.arange(len(df))

    fig, (
        ax_effective,
        ax_nominal,
        ax_predecode,
        ax_layer,
        ax_pacing_metrics,
        ax_loss,
        ax_queue,
        ax_residence,
    ) = plt.subplots(
        8,
        1,
        figsize=(16, 19),
        sharex=True,
        gridspec_kw={
            "height_ratios": [2.2, 2.2, 1.8, 1.2, 1.8, 1.2, 1.8, 1.8]
        },
    )

    # ============================================================
    # 1. renderMinusReceiveMs
    #    = render_time - receiveTimeMs
    #    전체 선 + late/drop만 circle
    # ============================================================
    ax_effective.plot(
        x,
        df["renderMinusReceiveMs"],
        label="renderMinusReceiveMs (= render_time - receiveTimeMs)",
        linewidth=2.0,
        alpha=0.9,
        linestyle="-.",
    )

    add_late_drop_circles(
        ax=ax_effective,
        x=x,
        df=df,
        y_col="renderMinusReceiveMs",
    )

    ax_effective.axhline(0, color="black", linewidth=1.0, alpha=0.45)
    ax_effective.set_ylabel("Milliseconds (ms)")
    ax_effective.set_title(
        "renderMinusReceiveMs "
        "(late normal / dropped frames circled)"
    )
    ax_effective.grid(True, alpha=0.3)
    ax_effective.legend(loc="best")

    # ============================================================
    # 2. decodeSlackNominalMs
    #    전체 선 + late/drop만 circle
    # ============================================================
    ax_nominal.plot(
        x,
        df["decodeSlackNominalMs"],
        label="decodeSlackNominalMs",
        linewidth=2.0,
        alpha=0.9,
        linestyle="-",
    )

    add_late_drop_circles(
        ax=ax_nominal,
        x=x,
        df=df,
        y_col="decodeSlackNominalMs",
    )

    ax_nominal.axhline(0, color="black", linewidth=1.0, alpha=0.45)
    ax_nominal.set_ylabel("Milliseconds (ms)")
    ax_nominal.set_title(
        "decodeSlackNominalMs "
        "(late normal / dropped frames circled)"
    )
    ax_nominal.grid(True, alpha=0.3)
    ax_nominal.legend(loc="best")

    # ============================================================
    # 3. preDecodeWaitingMs only
    #    adjusted slack 없음
    #    전체 선 + late/drop만 circle
    # ============================================================
    ax_predecode.plot(
        x,
        df["preDecodeWaitingMs"],
        label="preDecodeWaitingMs (= frameBufferInsertTimeMs - receiveTimeMs)",
        linewidth=2.0,
        alpha=0.9,
        linestyle="-",
    )

    add_late_drop_circles(
        ax=ax_predecode,
        x=x,
        df=df,
        y_col="preDecodeWaitingMs",
    )

    ax_predecode.axhline(0, color="black", linewidth=1.0, alpha=0.45)
    ax_predecode.set_ylabel("Milliseconds (ms)")
    ax_predecode.set_title(
        "preDecodeWaitingMs "
        "(late normal / dropped frames circled)"
    )
    ax_predecode.grid(True, alpha=0.3)
    ax_predecode.legend(loc="best")

    # ============================================================
    # 4. Spatial Layer / Available Bitrate
    #    기존 방식 그대로
    #    late/drop 구분 표시 안 함
    # ============================================================
    ax_layer.step(
        x,
        df["currentSpatialLayer"],
        label="currentSpatialLayer",
        linewidth=2.0,
        where="post",
        color="red",
    )

    ax_layer.set_ylabel("Spatial Layer")
    ax_layer.set_yticks([-1, 0, 1, 2])
    ax_layer.set_ylim(-1.2, 2.2)
    ax_layer.grid(True, alpha=0.3)

    ax_bitrate = ax_layer.twinx()
    ax_bitrate.plot(
        x,
        df["gccAvailableBitrateMbps"],
        label="GCC bitrate",
        linewidth=1.8,
        alpha=0.9,
        linestyle="--",
    )

    ax_bitrate.plot(
        x,
        df["camelAvailableBitrateMbps"],
        label="Camel bitrate",
        linewidth=1.8,
        alpha=0.9,
        linestyle=":",
    )
    ax_bitrate.set_ylabel("Available Bitrate (Mbps)")

    lines1, labels1 = ax_layer.get_legend_handles_labels()
    lines2, labels2 = ax_bitrate.get_legend_handles_labels()
    ax_layer.legend(lines1 + lines2, labels1 + labels2, loc="best")
    ax_layer.set_title("Spatial Layer and Available Bitrate")

    ## ax_bitrate.set_yscale("log")
    # ============================================================
    # 5. ACE / pacing / Camel burst metrics
    #    aceQueueBytes / pacingBacklogBytes / pacingBucketSizeBytes /
    #    camelBurstLengthBytes를 같은 칸에 함께 표시
    # ============================================================
    # ax_pacing_metrics.plot(
    #     x,
    #     df["aceQueueBytes"],
    #     label="aceQueueBytes",
    #     linewidth=2.0,
    #     alpha=0.9,
    #     linestyle="-",
    # )

    # ax_pacing_metrics.plot(
    #     x,
    #     df["pacingBacklogBytes"],
    #     label="pacingBacklogBytes",
    #     linewidth=2.0,
    #     alpha=0.9,
    #     linestyle="--",
    # )

    ax_pacing_metrics.plot(
        x,
        df["pacingBucketSizeBytes"],
        label="pacingBucketSizeBytes",
        linewidth=2.0,
        alpha=0.9,
        linestyle="-.",
    )

    ax_pacing_metrics.plot(
        x,
        df["camelBurstLengthBytes"],
        label="camelBurstLengthBytes",
        linewidth=2.0,
        alpha=0.9,
        linestyle=":",
    )

    ax_pacing_metrics.axhline(0, color="black", linewidth=1.0, alpha=0.45)
    ax_pacing_metrics.set_ylabel("Bytes")
    ax_pacing_metrics.set_title(
        "ACE Queue / Pacing Backlog / Pacing Bucket Size / Camel Burst Length"
    )
    ax_pacing_metrics.grid(True, alpha=0.3)
    ax_pacing_metrics.legend(loc="best")

    # ============================================================
    # 6. lossRate
    #    기존 방식 그대로
    #    late/drop 구분 표시 안 함
    # ============================================================
    ax_loss.plot(
        x,
        df["lossRate"],
        label="lossRate",
        linewidth=2.0,
        alpha=0.9,
        linestyle="-",
    )

    ax_loss.axhline(0, color="black", linewidth=1.0, alpha=0.45)
    ax_loss.set_ylabel("Loss rate")
    ax_loss.set_title("Packet Loss Rate")
    ax_loss.grid(True, alpha=0.3)
    ax_loss.legend(loc="best")
    # ============================================================
    # 7. queueResidenceMs
    #    기존 방식 그대로
    #    late/drop 구분 표시 안 함
    # ============================================================
    ax_queue.plot(
        x,
        df["queueResidenceMs"],
        label="queueResidenceMs (= decodeQueueResidenceMs + frameBufferResidenceMs)",
        linewidth=2.0,
        alpha=0.9,
        linestyle=":",
    )

    ax_queue.axhline(0, color="black", linewidth=1.0, alpha=0.45)
    ax_queue.set_ylabel("Milliseconds (ms)")
    ax_queue.set_title("queueResidenceMs")
    ax_queue.grid(True, alpha=0.3)
    ax_queue.legend(loc="best")

    # ============================================================
    # 8. decodeQueueResidenceMs / frameBufferResidenceMs
    #    기존 방식 그대로
    #    late/drop 구분 표시 안 함
    # ============================================================
    ax_residence.plot(
        x,
        df["decodeQueueResidenceMs"],
        label="decodeQueueResidenceMs",
        linewidth=2.0,
        alpha=0.9,
        linestyle="-",
    )

    ax_residence.plot(
        x,
        df["frameBufferResidenceMs"],
        label="frameBufferResidenceMs",
        linewidth=2.0,
        alpha=0.9,
        linestyle="--",
    )

    ax_residence.axhline(0, color="black", linewidth=1.0, alpha=0.45)
    ax_residence.set_ylabel("Residence (ms)")
    ax_residence.set_xlabel("Frame index")
    ax_residence.set_title("Decode Queue Residence and Frame Buffer Residence")
    ax_residence.grid(True, alpha=0.3)
    ax_residence.legend(loc="best")

    # ============================================================
    # spatial layer transition vertical lines
    # ============================================================
    prev_layer = df["currentSpatialLayer"].shift(1)
    transition_indices = df.index[df["currentSpatialLayer"] != prev_layer].tolist()

    for idx in transition_indices:
        if idx == 0:
            continue

        ax_effective.axvline(idx, linestyle=":", linewidth=1.0, alpha=0.45)
        ax_nominal.axvline(idx, linestyle=":", linewidth=1.0, alpha=0.45)
        ax_predecode.axvline(idx, linestyle=":", linewidth=1.0, alpha=0.45)
        ax_layer.axvline(idx, linestyle=":", linewidth=1.0, alpha=0.45)
        ax_pacing_metrics.axvline(idx, linestyle=":", linewidth=1.0, alpha=0.45)
        ax_loss.axvline(idx, linestyle=":", linewidth=1.0, alpha=0.45)
        ax_queue.axvline(idx, linestyle=":", linewidth=1.0, alpha=0.45)
        ax_residence.axvline(idx, linestyle=":", linewidth=1.0, alpha=0.45)

    # 분류 기준 설명
    ax_effective.text(
        0.01,
        0.03,
        f"LATE_NORMAL: non-dropped and max_wait <= {late_threshold_ms} ms\n"
        f"DROPPED: invalid decodeStart/decodeFinish/frameBufferExtract",
        transform=ax_effective.transAxes,
        fontsize=9,
        family="monospace",
        bbox={
            "boxstyle": "round,pad=0.35",
            "facecolor": "white",
            "edgecolor": "gray",
            "alpha": 0.8,
        },
    )

    plt.tight_layout()
    plt.savefig(out_path, dpi=200)
    plt.close()

    print(f"[INFO] Saved plot: {out_path}")


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Plot decode slack, preDecodeWaiting, spatial layer/bitrate, "
            "ACE/pacing/Camel burst, queueResidence, nominal slack, and residence metrics. "
            "Only late/dropped frames are circled where requested."
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
        help="Optional custom input CSV path",
    )

    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Optional custom output plot path",
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

    if args.output is not None:
        out_path = args.output
    else:
        if args.pacing == 1:
            out_path = "plots/effective_nominal_predecode_pacing_camel_metrics_residence_circled_pacing.png"
        else:
            out_path = "plots/effective_nominal_predecode_pacing_camel_metrics_residence_circled.png"

    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    print(f"[INFO] csv_path          : {csv_path}")
    print(f"[INFO] out_path          : {out_path}")
    print(f"[INFO] late_threshold_ms : {args.late_threshold_ms}")

    df = load_and_prepare_csv(
        csv_path=csv_path,
        late_threshold_ms=args.late_threshold_ms,
    )

    print_summary(df)

    plot_metrics(
        df=df,
        out_path=out_path,
        late_threshold_ms=args.late_threshold_ms,
    )


if __name__ == "__main__":
    main()