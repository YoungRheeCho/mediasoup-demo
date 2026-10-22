import os
import argparse
import numpy as np
import pandas as pd


INVALID_BIG_THRESHOLD = 1e18

DEFAULT_FRAME_CSV_PATH = "/home/n2sl/yeon/qos/network/log/frame/frame_records.csv"
DEFAULT_FRAME_CSV_PATH_PACING = "/home/n2sl/yeon/qos/network/log/frame/frame_records_pacing.csv"

DEFAULT_SUMMARY_PATH = "/home/n2sl/yeon/qos/network/log/frame/plots/render_receive_predecode_threshold_summary.txt"
DEFAULT_SUMMARY_PATH_PACING = "/home/n2sl/yeon/qos/network/log/frame/plots/render_receive_predecode_threshold_summary_pacing.txt"

DEFAULT_LATE_THRESHOLD_MS = -5.0

CLASS_SAFE_NORMAL = "SAFE_NORMAL"
CLASS_LATE_NORMAL = "LATE_NORMAL"
CLASS_DROPPED = "DROPPED"

CLASS_ORDER = [
    CLASS_SAFE_NORMAL,
    CLASS_LATE_NORMAL,
    CLASS_DROPPED,
]


def to_numeric_clean(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    s = s.mask(np.isinf(s), np.nan)
    s = s.mask(s.abs() > INVALID_BIG_THRESHOLD, np.nan)
    return s


def valid_time(df: pd.DataFrame, col: str) -> pd.Series:
    return df[col].notna() & (df[col] > 0)


def classify_dropped_frame(df: pd.DataFrame) -> pd.Series:
    """
    정상 decode frame이라면 아래 pipeline timestamp들이 모두 있어야 한다고 봄.
    하나라도 0/NaN이면 DROPPED.
    """
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

    df["isBadFrame"] = df["frameClass"].isin([CLASS_LATE_NORMAL, CLASS_DROPPED])
    df["isSafeFrame"] = df["frameClass"] == CLASS_SAFE_NORMAL

    # dropped subtype
    df["hasFrameBufferInsert"] = valid_time(df, "frameBufferInsertTimeMs")

    df["dropNoFrameBufferInsert"] = (
        (df["frameClass"] == CLASS_DROPPED)
        & (~df["hasFrameBufferInsert"])
    )

    df["dropAfterFrameBufferInsert"] = (
        (df["frameClass"] == CLASS_DROPPED)
        & df["hasFrameBufferInsert"]
    )

    # render_time - receiveTimeMs
    df["renderMinusReceiveMs"] = np.nan
    valid_rr = valid_time(df, "render_time") & valid_time(df, "receiveTimeMs")
    df.loc[valid_rr, "renderMinusReceiveMs"] = (
        df.loc[valid_rr, "render_time"]
        - df.loc[valid_rr, "receiveTimeMs"]
    )

    # preDecodeWaitingMs = frameBufferInsertTimeMs - receiveTimeMs
    # frameBufferInsertTimeMs <= 0이면 frame buffer에 못 들어간 것이므로 NaN.
    df["preDecodeWaitingMs"] = np.nan
    valid_predecode = (
        valid_time(df, "frameBufferInsertTimeMs")
        & valid_time(df, "receiveTimeMs")
    )
    df.loc[valid_predecode, "preDecodeWaitingMs"] = (
        df.loc[valid_predecode, "frameBufferInsertTimeMs"]
        - df.loc[valid_predecode, "receiveTimeMs"]
    )

    # 말이 안 되는 음수는 invalid 처리
    df.loc[df["preDecodeWaitingMs"] < 0, "preDecodeWaitingMs"] = np.nan

    # render_time - frameBufferInsertTimeMs
    # 즉, predecode 이후 render까지 남은 margin
    df["renderMinusFrameBufferInsertMs"] = np.nan
    valid_ri = (
        valid_time(df, "render_time")
        & valid_time(df, "frameBufferInsertTimeMs")
    )
    df.loc[valid_ri, "renderMinusFrameBufferInsertMs"] = (
        df.loc[valid_ri, "render_time"]
        - df.loc[valid_ri, "frameBufferInsertTimeMs"]
    )

    return df


def fmt(v, digits=3) -> str:
    if pd.isna(v):
        return "NaN"
    return f"{v:.{digits}f}"


def describe_series(series: pd.Series) -> dict:
    s = series.dropna()

    if len(s) == 0:
        return {
            "count": 0,
            "mean": np.nan,
            "std": np.nan,
            "min": np.nan,
            "p01": np.nan,
            "p05": np.nan,
            "p10": np.nan,
            "p25": np.nan,
            "median": np.nan,
            "p75": np.nan,
            "p90": np.nan,
            "p95": np.nan,
            "p99": np.nan,
            "max": np.nan,
        }

    return {
        "count": int(len(s)),
        "mean": float(s.mean()),
        "std": float(s.std()),
        "min": float(s.min()),
        "p01": float(s.quantile(0.01)),
        "p05": float(s.quantile(0.05)),
        "p10": float(s.quantile(0.10)),
        "p25": float(s.quantile(0.25)),
        "median": float(s.median()),
        "p75": float(s.quantile(0.75)),
        "p90": float(s.quantile(0.90)),
        "p95": float(s.quantile(0.95)),
        "p99": float(s.quantile(0.99)),
        "max": float(s.max()),
    }


def write_distribution_table(f, title: str, df: pd.DataFrame, metric_col: str) -> None:
    f.write(f"[{title}: {metric_col}]\n")
    f.write(
        f"{'Class':<20} {'count':>8} {'mean':>10} {'std':>10} "
        f"{'min':>10} {'p05':>10} {'p25':>10} {'median':>10} "
        f"{'p75':>10} {'p95':>10} {'p99':>10} {'max':>10}\n"
    )
    f.write("-" * 135 + "\n")

    rows = [
        ("ALL_VALID", df[df[metric_col].notna()]),
        ("SAFE_NORMAL", df[(df["frameClass"] == CLASS_SAFE_NORMAL) & df[metric_col].notna()]),
        ("LATE_NORMAL", df[(df["frameClass"] == CLASS_LATE_NORMAL) & df[metric_col].notna()]),
        ("DROPPED", df[(df["frameClass"] == CLASS_DROPPED) & df[metric_col].notna()]),
        ("BAD", df[df["isBadFrame"] & df[metric_col].notna()]),
    ]

    for name, g in rows:
        s = describe_series(g[metric_col])
        f.write(
            f"{name:<20} "
            f"{s['count']:>8} "
            f"{fmt(s['mean']):>10} "
            f"{fmt(s['std']):>10} "
            f"{fmt(s['min']):>10} "
            f"{fmt(s['p05']):>10} "
            f"{fmt(s['p25']):>10} "
            f"{fmt(s['median']):>10} "
            f"{fmt(s['p75']):>10} "
            f"{fmt(s['p95']):>10} "
            f"{fmt(s['p99']):>10} "
            f"{fmt(s['max']):>10}\n"
        )

    f.write("\n")


def candidate_values(series: pd.Series, max_candidates: int = 250) -> np.ndarray:
    s = series.dropna()

    if len(s) == 0:
        return np.array([])

    unique = np.sort(s.unique())

    if len(unique) <= max_candidates:
        return unique

    qs = np.linspace(0.0, 1.0, max_candidates)
    vals = np.quantile(s, qs)
    vals = np.unique(vals)

    return np.sort(vals)


def threshold_stats(subset: pd.DataFrame, min_support: int) -> dict:
    safe_count = int((subset["frameClass"] == CLASS_SAFE_NORMAL).sum())
    late_count = int((subset["frameClass"] == CLASS_LATE_NORMAL).sum())
    drop_count = int((subset["frameClass"] == CLASS_DROPPED).sum())
    bad_count = late_count + drop_count
    total = len(subset)

    return {
        "support": int(total),
        "min_support": int(min_support),
        "safe_count": safe_count,
        "late_count": late_count,
        "drop_count": drop_count,
        "bad_count": bad_count,
        "safe_rate": safe_count / total if total > 0 else np.nan,
        "bad_rate": bad_count / total if total > 0 else np.nan,
    }


def find_ge_threshold(
    df: pd.DataFrame,
    metric_col: str,
    target_safe_rate: float,
    min_support_abs: int,
    min_support_ratio: float,
):
    """
    metric_col >= T 조건에서 SAFE 비율이 target 이상이 되는 가장 작은 T 탐색.
    renderMinusReceiveMs, renderMinusFrameBufferInsertMs처럼 값이 클수록 좋은 metric에 사용.
    """
    valid_df = df[df[metric_col].notna()].copy()

    if len(valid_df) == 0:
        return None

    min_support = max(
        min_support_abs,
        int(np.ceil(len(valid_df) * min_support_ratio)),
    )

    for threshold in candidate_values(valid_df[metric_col]):
        subset = valid_df[valid_df[metric_col] >= threshold]

        if len(subset) < min_support:
            continue

        stats = threshold_stats(subset, min_support)

        if stats["safe_rate"] >= target_safe_rate:
            stats["threshold"] = float(threshold)
            stats["condition"] = f"{metric_col} >= T"
            stats["metric_col"] = metric_col
            return stats

    return None


def find_le_threshold(
    df: pd.DataFrame,
    metric_col: str,
    target_safe_rate: float,
    min_support_abs: int,
    min_support_ratio: float,
):
    """
    metric_col <= T 조건에서 SAFE 비율이 target 이상이 되는 가장 큰 T 탐색.
    preDecodeWaitingMs처럼 값이 작을수록 좋은 metric에 사용.
    """
    valid_df = df[df[metric_col].notna()].copy()

    if len(valid_df) == 0:
        return None

    min_support = max(
        min_support_abs,
        int(np.ceil(len(valid_df) * min_support_ratio)),
    )

    best = None

    for threshold in candidate_values(valid_df[metric_col]):
        subset = valid_df[valid_df[metric_col] <= threshold]

        if len(subset) < min_support:
            continue

        stats = threshold_stats(subset, min_support)

        if stats["safe_rate"] >= target_safe_rate:
            stats["threshold"] = float(threshold)
            stats["condition"] = f"{metric_col} <= T"
            stats["metric_col"] = metric_col
            best = stats

    return best


def find_2d_threshold(
    df: pd.DataFrame,
    target_safe_rate: float,
    min_support_abs: int,
    min_support_ratio: float,
):
    """
    2D 조건 탐색:

    renderMinusReceiveMs >= R
    preDecodeWaitingMs <= P

    즉, receive margin은 충분히 크고,
    predecode delay는 충분히 작아야 한다는 조건.

    후보 중 SAFE rate >= target을 만족하면서 support가 가장 큰 조건을 선택.
    """
    metric_r = "renderMinusReceiveMs"
    metric_p = "preDecodeWaitingMs"

    valid_df = df[
        df[metric_r].notna()
        & df[metric_p].notna()
    ].copy()

    if len(valid_df) == 0:
        return None

    min_support = max(
        min_support_abs,
        int(np.ceil(len(valid_df) * min_support_ratio)),
    )

    r_candidates = candidate_values(valid_df[metric_r], max_candidates=120)
    p_candidates = candidate_values(valid_df[metric_p], max_candidates=120)

    best = None

    for r in r_candidates:
        r_subset = valid_df[valid_df[metric_r] >= r]

        if len(r_subset) < min_support:
            continue

        for p in p_candidates:
            subset = r_subset[r_subset[metric_p] <= p]

            if len(subset) < min_support:
                continue

            stats = threshold_stats(subset, min_support)

            if stats["safe_rate"] < target_safe_rate:
                continue

            candidate = dict(stats)
            candidate["render_threshold"] = float(r)
            candidate["predecode_threshold"] = float(p)
            candidate["condition"] = (
                "renderMinusReceiveMs >= R AND preDecodeWaitingMs <= P"
            )

            if best is None:
                best = candidate
                continue

            # support가 큰 조건을 우선 선택
            # support가 같으면 더 느슨한 조건 선택:
            # R은 작을수록 느슨하고, P는 클수록 느슨함.
            if candidate["support"] > best["support"]:
                best = candidate
            elif candidate["support"] == best["support"]:
                if (
                    candidate["render_threshold"] < best["render_threshold"]
                    or candidate["predecode_threshold"] > best["predecode_threshold"]
                ):
                    best = candidate

    return best


def write_threshold_result(f, title: str, result) -> None:
    f.write(f"[{title}]\n")

    if result is None:
        f.write("No threshold found under current support condition.\n\n")
        return

    if "threshold" in result:
        f.write(f"metric                       : {result['metric_col']}\n")
        f.write(f"threshold T                  : {fmt(result['threshold'])} ms\n")
        f.write(f"condition                    : {result['condition']}\n")

    if "render_threshold" in result:
        f.write(f"render threshold R           : {fmt(result['render_threshold'])} ms\n")
        f.write(f"predecode threshold P        : {fmt(result['predecode_threshold'])} ms\n")
        f.write(f"condition                    : {result['condition']}\n")

    f.write(f"support frames               : {result['support']}\n")
    f.write(f"min support required          : {result['min_support']}\n")
    f.write(f"safe count                   : {result['safe_count']}\n")
    f.write(f"late normal count             : {result['late_count']}\n")
    f.write(f"dropped count                 : {result['drop_count']}\n")
    f.write(f"bad count                     : {result['bad_count']}\n")
    f.write(f"safe rate                     : {result['safe_rate']:.6f}\n")
    f.write(f"bad rate                      : {result['bad_rate']:.6f}\n")
    f.write("\n")


def make_2d_bucket_table(
    df: pd.DataFrame,
    render_bin_width_ms: float,
    predecode_bin_width_ms: float,
) -> pd.DataFrame:
    valid_df = df[
        df["renderMinusReceiveMs"].notna()
        & df["preDecodeWaitingMs"].notna()
    ].copy()

    if len(valid_df) == 0:
        return pd.DataFrame()

    r_min = np.floor(valid_df["renderMinusReceiveMs"].min() / render_bin_width_ms) * render_bin_width_ms
    r_max = np.ceil(valid_df["renderMinusReceiveMs"].max() / render_bin_width_ms) * render_bin_width_ms

    p_min = np.floor(valid_df["preDecodeWaitingMs"].min() / predecode_bin_width_ms) * predecode_bin_width_ms
    p_max = np.ceil(valid_df["preDecodeWaitingMs"].max() / predecode_bin_width_ms) * predecode_bin_width_ms

    r_bins = np.arange(r_min, r_max + render_bin_width_ms, render_bin_width_ms)
    p_bins = np.arange(p_min, p_max + predecode_bin_width_ms, predecode_bin_width_ms)

    if len(r_bins) < 2:
        r_bins = np.array([r_min, r_min + render_bin_width_ms])
    if len(p_bins) < 2:
        p_bins = np.array([p_min, p_min + predecode_bin_width_ms])

    valid_df["renderBucket"] = pd.cut(
        valid_df["renderMinusReceiveMs"],
        bins=r_bins,
        include_lowest=True,
        right=False,
    )

    valid_df["predecodeBucket"] = pd.cut(
        valid_df["preDecodeWaitingMs"],
        bins=p_bins,
        include_lowest=True,
        right=False,
    )

    rows = []

    grouped = valid_df.groupby(["renderBucket", "predecodeBucket"], observed=True)

    for (rb, pb), g in grouped:
        total = len(g)
        safe = int((g["frameClass"] == CLASS_SAFE_NORMAL).sum())
        late = int((g["frameClass"] == CLASS_LATE_NORMAL).sum())
        drop = int((g["frameClass"] == CLASS_DROPPED).sum())
        bad = late + drop

        rows.append({
            "renderBucket": str(rb),
            "predecodeBucket": str(pb),
            "total": total,
            "safe": safe,
            "late": late,
            "drop": drop,
            "bad": bad,
            "safeRate": safe / total if total > 0 else np.nan,
            "badRate": bad / total if total > 0 else np.nan,
        })

    out = pd.DataFrame(rows)

    if len(out) > 0:
        out = out.sort_values(["renderBucket", "predecodeBucket"])

    return out


def save_summary_txt(
    df: pd.DataFrame,
    summary_path: str,
    late_threshold_ms: float,
    target_safe_rates,
    min_support_abs: int,
    min_support_ratio: float,
    render_bin_width_ms: float,
    predecode_bin_width_ms: float,
) -> None:
    os.makedirs(os.path.dirname(summary_path), exist_ok=True)

    valid_render_df = df[df["renderMinusReceiveMs"].notna()].copy()
    valid_predecode_df = df[df["preDecodeWaitingMs"].notna()].copy()
    valid_insert_margin_df = df[df["renderMinusFrameBufferInsertMs"].notna()].copy()
    valid_both_df = df[
        df["renderMinusReceiveMs"].notna()
        & df["preDecodeWaitingMs"].notna()
    ].copy()

    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("render-receive + predecode threshold analysis\n")
        f.write("=============================================\n\n")

        f.write("[Goal]\n")
        f.write(
            "Analyze not only render_time - receiveTimeMs, "
            "but also preDecodeWaitingMs = frameBufferInsertTimeMs - receiveTimeMs.\n"
        )
        f.write(
            "The key derived metric is render_time - frameBufferInsertTimeMs, "
            "which means the remaining margin after predecode.\n\n"
        )

        f.write("[Metric definitions]\n")
        f.write("renderMinusReceiveMs = render_time - receiveTimeMs\n")
        f.write("  larger value = more margin at receive time\n\n")

        f.write("preDecodeWaitingMs = frameBufferInsertTimeMs - receiveTimeMs\n")
        f.write("  larger value = longer delay before the frame enters frame buffer\n")
        f.write("  if frameBufferInsertTimeMs <= 0, this metric is NaN\n\n")

        f.write("renderMinusFrameBufferInsertMs = render_time - frameBufferInsertTimeMs\n")
        f.write("  equivalent to renderMinusReceiveMs - preDecodeWaitingMs\n")
        f.write("  larger value = more remaining margin after frame buffer insert\n\n")

        f.write("[Frame class definition]\n")
        f.write(f"SAFE_NORMAL : non-dropped and max_wait >  {late_threshold_ms} ms\n")
        f.write(f"LATE_NORMAL : non-dropped and max_wait <= {late_threshold_ms} ms\n")
        f.write("DROPPED     : one or more pipeline timestamps are missing/zero\n\n")

        f.write("[Overall counts]\n")
        f.write(f"total rows                              : {len(df)}\n")
        f.write(f"SAFE_NORMAL total                       : {int((df['frameClass'] == CLASS_SAFE_NORMAL).sum())}\n")
        f.write(f"LATE_NORMAL total                       : {int((df['frameClass'] == CLASS_LATE_NORMAL).sum())}\n")
        f.write(f"DROPPED total                           : {int((df['frameClass'] == CLASS_DROPPED).sum())}\n")
        f.write(f"BAD total                               : {int(df['isBadFrame'].sum())}\n")
        f.write(f"valid renderMinusReceive rows           : {len(valid_render_df)}\n")
        f.write(f"valid preDecodeWaiting rows             : {len(valid_predecode_df)}\n")
        f.write(f"valid renderMinusFrameBufferInsert rows : {len(valid_insert_margin_df)}\n")
        f.write(f"valid render+predecode rows             : {len(valid_both_df)}\n")
        f.write(f"DROPPED no frameBufferInsert total      : {int(df['dropNoFrameBufferInsert'].sum())}\n")
        f.write(f"DROPPED after frameBufferInsert total   : {int(df['dropAfterFrameBufferInsert'].sum())}\n\n")

        f.write("[Distribution by class]\n\n")
        write_distribution_table(
            f,
            "Receive margin",
            df,
            "renderMinusReceiveMs",
        )
        write_distribution_table(
            f,
            "Predecode delay",
            df,
            "preDecodeWaitingMs",
        )
        write_distribution_table(
            f,
            "Remaining margin after frameBufferInsert",
            df,
            "renderMinusFrameBufferInsertMs",
        )

        f.write("[Observed bad boundaries]\n\n")

        bad_render = df[df["isBadFrame"] & df["renderMinusReceiveMs"].notna()]
        bad_predecode = df[df["isBadFrame"] & df["preDecodeWaitingMs"].notna()]
        bad_insert_margin = df[df["isBadFrame"] & df["renderMinusFrameBufferInsertMs"].notna()]

        if len(bad_render) > 0:
            f.write(f"max bad renderMinusReceiveMs           : {fmt(bad_render['renderMinusReceiveMs'].max())} ms\n")
        else:
            f.write("max bad renderMinusReceiveMs           : NaN\n")

        if len(bad_predecode) > 0:
            f.write(f"min bad preDecodeWaitingMs             : {fmt(bad_predecode['preDecodeWaitingMs'].min())} ms\n")
            f.write(f"max bad preDecodeWaitingMs             : {fmt(bad_predecode['preDecodeWaitingMs'].max())} ms\n")
        else:
            f.write("min/max bad preDecodeWaitingMs         : NaN\n")

        if len(bad_insert_margin) > 0:
            f.write(f"max bad renderMinusFrameBufferInsertMs : {fmt(bad_insert_margin['renderMinusFrameBufferInsertMs'].max())} ms\n")
            f.write(
                "Interpretation: if BAD exists even with large receive margin, "
                "check whether remaining margin after frameBufferInsert is small.\n"
            )
        else:
            f.write("max bad renderMinusFrameBufferInsertMs : NaN\n")

        f.write("\n")

        f.write("[1D threshold candidates]\n\n")

        for target in target_safe_rates:
            result = find_ge_threshold(
                df=df,
                metric_col="renderMinusReceiveMs",
                target_safe_rate=target,
                min_support_abs=min_support_abs,
                min_support_ratio=min_support_ratio,
            )
            write_threshold_result(
                f,
                f"renderMinusReceiveMs threshold for SAFE rate >= {target:.3f}",
                result,
            )

            result = find_le_threshold(
                df=df,
                metric_col="preDecodeWaitingMs",
                target_safe_rate=target,
                min_support_abs=min_support_abs,
                min_support_ratio=min_support_ratio,
            )
            write_threshold_result(
                f,
                f"preDecodeWaitingMs threshold for SAFE rate >= {target:.3f}",
                result,
            )

            result = find_ge_threshold(
                df=df,
                metric_col="renderMinusFrameBufferInsertMs",
                target_safe_rate=target,
                min_support_abs=min_support_abs,
                min_support_ratio=min_support_ratio,
            )
            write_threshold_result(
                f,
                f"renderMinusFrameBufferInsertMs threshold for SAFE rate >= {target:.3f}",
                result,
            )

        f.write("[2D threshold candidates]\n\n")
        f.write("Condition: renderMinusReceiveMs >= R AND preDecodeWaitingMs <= P\n")
        f.write("This checks both receive margin and predecode delay together.\n\n")

        for target in target_safe_rates:
            result = find_2d_threshold(
                df=df,
                target_safe_rate=target,
                min_support_abs=min_support_abs,
                min_support_ratio=min_support_ratio,
            )
            write_threshold_result(
                f,
                f"2D threshold for SAFE rate >= {target:.3f}",
                result,
            )

        f.write("[2D bucket distribution]\n")
        f.write(f"render bin width    : {render_bin_width_ms} ms\n")
        f.write(f"predecode bin width : {predecode_bin_width_ms} ms\n")
        f.write(
            f"{'renderBucket':<22} {'predecodeBucket':<22} "
            f"{'total':>7} {'safe':>7} {'late':>7} {'drop':>7} "
            f"{'bad':>7} {'safeRate':>10} {'badRate':>10}\n"
        )
        f.write("-" * 110 + "\n")

        bucket_df = make_2d_bucket_table(
            df=df,
            render_bin_width_ms=render_bin_width_ms,
            predecode_bin_width_ms=predecode_bin_width_ms,
        )

        if len(bucket_df) == 0:
            f.write("No valid 2D bucket data.\n")
        else:
            for _, row in bucket_df.iterrows():
                f.write(
                    f"{row['renderBucket']:<22} "
                    f"{row['predecodeBucket']:<22} "
                    f"{int(row['total']):>7} "
                    f"{int(row['safe']):>7} "
                    f"{int(row['late']):>7} "
                    f"{int(row['drop']):>7} "
                    f"{int(row['bad']):>7} "
                    f"{row['safeRate']:>10.4f} "
                    f"{row['badRate']:>10.4f}\n"
                )

        f.write("\n[Important note]\n")
        f.write(
            "renderMinusReceiveMs alone tells whether receive was early enough. "
            "preDecodeWaitingMs tells how much of that margin was consumed before frameBufferInsert. "
            "renderMinusFrameBufferInsertMs is often the more direct metric because it measures "
            "the remaining time margin after predecode.\n"
        )

    print(f"[INFO] Saved summary: {summary_path}")


def print_console_summary(df: pd.DataFrame) -> None:
    valid_render = df[df["renderMinusReceiveMs"].notna()]
    valid_predecode = df[df["preDecodeWaitingMs"].notna()]
    valid_insert_margin = df[df["renderMinusFrameBufferInsertMs"].notna()]

    print("\n[SUMMARY]")
    print(f"total rows                              : {len(df)}")
    print(f"SAFE_NORMAL                             : {int((df['frameClass'] == CLASS_SAFE_NORMAL).sum())}")
    print(f"LATE_NORMAL                             : {int((df['frameClass'] == CLASS_LATE_NORMAL).sum())}")
    print(f"DROPPED                                 : {int((df['frameClass'] == CLASS_DROPPED).sum())}")
    print(f"valid renderMinusReceive rows           : {len(valid_render)}")
    print(f"valid preDecodeWaiting rows             : {len(valid_predecode)}")
    print(f"valid renderMinusFrameBufferInsert rows : {len(valid_insert_margin)}")

    bad_insert_margin = df[df["isBadFrame"] & df["renderMinusFrameBufferInsertMs"].notna()]
    if len(bad_insert_margin) > 0:
        print(
            f"max bad renderMinusFrameBufferInsertMs  : "
            f"{bad_insert_margin['renderMinusFrameBufferInsertMs'].max():.3f} ms"
        )


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Analyze render_time - receiveTimeMs together with "
            "preDecodeWaitingMs = frameBufferInsertTimeMs - receiveTimeMs."
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

    parser.add_argument(
        "--target-safe-rates",
        type=float,
        nargs="+",
        default=[0.95, 0.99],
        help="Target SAFE_NORMAL rates for threshold search. Default: 0.95 0.99",
    )

    parser.add_argument(
        "--min-support-abs",
        type=int,
        default=30,
        help="Minimum number of frames required above/below threshold. Default: 30",
    )

    parser.add_argument(
        "--min-support-ratio",
        type=float,
        default=0.05,
        help="Minimum ratio of valid frames required. Default: 0.05",
    )

    parser.add_argument(
        "--render-bin-width-ms",
        type=float,
        default=5.0,
        help="Bucket width for renderMinusReceiveMs. Default: 5 ms",
    )

    parser.add_argument(
        "--predecode-bin-width-ms",
        type=float,
        default=2.0,
        help="Bucket width for preDecodeWaitingMs. Default: 2 ms",
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

    print(f"[INFO] csv_path              : {csv_path}")
    print(f"[INFO] summary_path          : {summary_path}")
    print(f"[INFO] late_threshold_ms     : {args.late_threshold_ms}")
    print(f"[INFO] target_safe_rates     : {args.target_safe_rates}")
    print(f"[INFO] min_support_abs       : {args.min_support_abs}")
    print(f"[INFO] min_support_ratio     : {args.min_support_ratio}")

    df = load_and_prepare_csv(
        csv_path=csv_path,
        late_threshold_ms=args.late_threshold_ms,
    )

    print_console_summary(df)

    save_summary_txt(
        df=df,
        summary_path=summary_path,
        late_threshold_ms=args.late_threshold_ms,
        target_safe_rates=args.target_safe_rates,
        min_support_abs=args.min_support_abs,
        min_support_ratio=args.min_support_ratio,
        render_bin_width_ms=args.render_bin_width_ms,
        predecode_bin_width_ms=args.predecode_bin_width_ms,
    )


if __name__ == "__main__":
    main()