import os
import argparse
import pandas as pd
import matplotlib.pyplot as plt


def load_and_prepare_csv(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df.columns = df.columns.str.strip()

    required_cols = [
        "frameId",
        "receiveTimeMs",
        "frameBufferInsertTimeMs",
        "frameBufferExtractTimeMs",
        "latestDecodeTimeMs",
        "actualSlackMs",
        "actualSlackEffectiveMs",
        "decodeSlackEffectiveMs",
        "decodeQueueResidenceMs",
        "frameBufferResidenceMs",
        "currentSpatialLayer",
        "availableBitrateBps",
    ]

    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns in CSV: {missing}")

    for col in required_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # 수정된 정의
    df["preDecodeWaitingMs"] = df["frameBufferInsertTimeMs"] - df["receiveTimeMs"]

    # 새 지표
    df["actualSlackMinusPreDecodeWaitingMs"] = (
        df["actualSlackMs"] - df["preDecodeWaitingMs"]
    )

    # queueResidence 직접 계산
    df["queueResidenceMs"] = (
        df["decodeQueueResidenceMs"] + df["frameBufferResidenceMs"]
    )

    # frameBufferExtract - latestDecode
    df["frameBufferExtractMinusLatestDecodeMs"] = (
        df["frameBufferExtractTimeMs"] - df["latestDecodeTimeMs"]
    )

    df["availableBitrateMbps"] = df["availableBitrateBps"] / 1_000_000.0

    df = df.dropna(
        subset=[
            "frameId",
            "preDecodeWaitingMs",
            "actualSlackMs",
            "actualSlackMinusPreDecodeWaitingMs",
            "actualSlackEffectiveMs",
            "decodeSlackEffectiveMs",
            "decodeQueueResidenceMs",
            "frameBufferResidenceMs",
            "queueResidenceMs",
            "frameBufferExtractMinusLatestDecodeMs",
            "currentSpatialLayer",
            "availableBitrateMbps",
        ]
    ).copy()

    df["currentSpatialLayer"] = df["currentSpatialLayer"].astype(int)
    df = df.sort_values("frameId").reset_index(drop=True)

    return df


def plot_metrics_with_spatial_and_bitrate(df: pd.DataFrame, out_path: str) -> None:
    x = range(len(df))

    fig, (ax1, ax_mid, ax_layer, ax_res, ax_latest) = plt.subplots(
        5,
        1,
        figsize=(16, 14),
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1.8, 1, 1.6, 1.6]},
    )

    # ===== 1st plot =====
    ax1.plot(
        x,
        df["decodeSlackEffectiveMs"],
        label="decodeSlackEffectiveMs",
        linewidth=2.0,
        alpha=0.9,
        linestyle="-.",
    )

    ax1.plot(
        x,
        df["queueResidenceMs"],
        label="queueResidenceMs (= decodeQueueResidenceMs + frameBufferResidenceMs)",
        linewidth=2.0,
        alpha=0.9,
        linestyle=":",
    )

    ax1.set_ylabel("Milliseconds (ms)")
    ax1.set_title("Effective Slack / Queue / PreDecodeWaiting / SpatialLayer / Residences / LatestDecode")
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc="best")

    # ===== 2nd plot =====
    ax_mid.plot(
        x,
        df["preDecodeWaitingMs"],
        label="preDecodeWaitingMs",
        linewidth=1.8,
        alpha=0.9,
        linestyle="-",
    )

    ax_mid.plot(
        x,
        df["actualSlackMinusPreDecodeWaitingMs"],
        label="actualSlackMs - preDecodeWaitingMs",
        linewidth=2.0,
        alpha=0.95,
        linestyle="--",
    )

    ax_mid.set_ylabel("Milliseconds (ms)")
    ax_mid.set_title("PreDecodeWaiting and Adjusted Actual Slack")
    ax_mid.grid(True, alpha=0.3)
    ax_mid.legend(loc="best")

    # ===== 3rd plot (원래 5번째) =====
    ax_layer.step(
        x,
        df["currentSpatialLayer"],
        label="currentSpatialLayer",
        linewidth=2.0,
        where="post",
        color="red",
    )

    ax_layer.set_ylabel("Spatial Layer")
    ax_layer.set_yticks([0, 1, 2])
    ax_layer.set_ylim(-0.2, 2.2)
    ax_layer.grid(True, alpha=0.3)

    ax3 = ax_layer.twinx()
    ax3.plot(
        x,
        df["availableBitrateMbps"],
        label="availableBitrateMbps",
        linewidth=1.5,
        alpha=0.8,
        linestyle="-",
        color="blue",
    )
    ax3.set_ylabel("Available Bitrate (Mbps)")

    lines2, labels2 = ax_layer.get_legend_handles_labels()
    lines3, labels3 = ax3.get_legend_handles_labels()
    ax_layer.legend(lines2 + lines3, labels2 + labels3, loc="best")
    ax_layer.set_title("Spatial Layer and Available Bitrate")

    # ===== 4th plot (원래 3번째) =====
    ax_res.plot(
        x,
        df["decodeQueueResidenceMs"],
        label="decodeQueueResidenceMs",
        linewidth=2.0,
        alpha=0.9,
        linestyle="-",
    )

    ax_res.plot(
        x,
        df["frameBufferResidenceMs"],
        label="frameBufferResidenceMs",
        linewidth=2.0,
        alpha=0.9,
        linestyle="--",
    )

    ax_res.set_ylabel("Residence (ms)")
    ax_res.set_title("Decode Queue Residence and Frame Buffer Residence")
    ax_res.grid(True, alpha=0.3)
    ax_res.legend(loc="best")

    # ===== 5th plot (원래 4번째) =====
    ax_latest.plot(
        x,
        df["frameBufferExtractMinusLatestDecodeMs"],
        label="frameBufferExtractTimeMs - latestDecodeTimeMs",
        linewidth=2.0,
        alpha=0.9,
        linestyle="-",
    )

    ax_latest.set_ylabel("Milliseconds (ms)")
    ax_latest.set_xlabel("Frame index")
    ax_latest.set_title("FrameBufferExtract - LatestDecode")
    ax_latest.grid(True, alpha=0.3)
    ax_latest.legend(loc="best")

    # spatial layer transition vertical lines
    prev_layer = df["currentSpatialLayer"].shift(1)
    transition_indices = df.index[df["currentSpatialLayer"] != prev_layer].tolist()

    for idx in transition_indices:
        if idx == 0:
            continue
        ax1.axvline(idx, linestyle=":", linewidth=1.0, alpha=0.5)
        ax_mid.axvline(idx, linestyle=":", linewidth=1.0, alpha=0.5)
        ax_layer.axvline(idx, linestyle=":", linewidth=1.0, alpha=0.5)
        ax_res.axvline(idx, linestyle=":", linewidth=1.0, alpha=0.5)
        ax_latest.axvline(idx, linestyle=":", linewidth=1.0, alpha=0.5)

    plt.tight_layout()
    plt.savefig(out_path, dpi=200)
    plt.close()

    print(f"[INFO] Saved plot: {out_path}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Plot slack metrics, queue metrics, preDecodeWaiting, spatial layer, residences, and latestDecode"
    )
    parser.add_argument(
        "-pacing",
        type=int,
        default=0,
        help="Use pacing CSV if set to 1. Default: 0",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.pacing == 1:
        csv_path = "/home/n2sl/yeon/qos/network/log/frame/frame_records_pacing.csv"
        out_path = "/home/n2sl/yeon/qos/network/log/frame/plots/effective_metrics_spatial_bitrate_pacing.png"
    else:
        csv_path = "/home/n2sl/yeon/qos/network/log/frame/frame_records.csv"
        out_path = "/home/n2sl/yeon/qos/network/log/frame/plots/effective_metrics_spatial_bitrate.png"

    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    print(f"[INFO] csv_path: {csv_path}")

    df = load_and_prepare_csv(csv_path)

    print(f"[INFO] loaded rows: {len(df)}")

    plot_metrics_with_spatial_and_bitrate(df, out_path)


if __name__ == "__main__":
    main()