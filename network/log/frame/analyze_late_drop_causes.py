## ANALYZE LATE OR DROP CAUSES

import os
import numpy as np
import pandas as pd


FRAME_RECORDS_CSV = "csv/frame_records.csv"
FRAME_PACKETS_CSV = "csv/frame_packets.csv"
FRAME_DROP_CSV = "csv/frame_drop_only.csv"

OUT_CSV = "csv/late_drop_cause_analysis.csv"
OUT_TXT = "txt/late_drop_cause_summary.txt"

LATE_THRESHOLD_MS = -5.0

PACKET_SPAN_JITTER_MS = 10.0
PREDECODE_DELAY_MS = 30.0
FRAME_BUFFER_DELAY_MS = 10.0
DECODE_QUEUE_DELAY_MS = 10.0
DECODE_TIME_DELAY_MS = 10.0


def clean_columns(df):
    df = df.copy()
    df.columns = df.columns.str.strip()
    return df


def to_num(df, cols):
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def classify_dropped(df):
    required = [
        "frameBufferInsertTimeMs",
        "frameBufferExtractTimeMs",
        "decodeQueueInsertTimeMs",
        "decodeQueueExtractTimeMs",
        "decodeStartMs",
        "decodeFinishMs",
    ]

    dropped = pd.Series(False, index=df.index)

    for col in required:
        if col in df.columns:
            dropped = dropped | df[col].isna() | (df[col] <= 0)

    return dropped

def primary_cause(row):
    missing = row.get("missing_seq_count", 0)
    packet_span = row.get("packet_span_ms", np.nan)
    predecode = row.get("preDecodeWaitingMs", np.nan)
    fb_res = row.get("frameBufferResidenceMs", np.nan)
    dq_res = row.get("decodeQueueResidenceMs", np.nan)
    decode_time = row.get("decodeTimeMs", np.nan)
    render_minus_receive = row.get("renderMinusReceiveMs", np.nan)
    receive_slack = row.get("receiveSlackMs", np.nan)
    decode_slack = row.get("decodeSlackNominalMs", np.nan)
    max_wait = row.get("max_wait", np.nan)

    fb_insert = row.get("frameBufferInsertTimeMs", np.nan)
    fb_extract = row.get("frameBufferExtractTimeMs", np.nan)
    dq_insert = row.get("decodeQueueInsertTimeMs", np.nan)
    dq_extract = row.get("decodeQueueExtractTimeMs", np.nan)
    decode_start = row.get("decodeStartMs", np.nan)
    decode_finish = row.get("decodeFinishMs", np.nan)

    # Network/packet side
    if pd.notna(missing) and missing > 0:
        return "PACKET_LOSS_OR_REORDER"

    if pd.notna(packet_span) and packet_span > PACKET_SPAN_JITTER_MS:
        return "PACKET_SPAN_JITTER"

    # Receive deadline side
    if pd.notna(render_minus_receive) and render_minus_receive <= 0:
        return "RECEIVE_AFTER_RENDER_TIME"

    if pd.notna(render_minus_receive) and render_minus_receive <= 15:
        return "LOW_RECEIVE_TO_RENDER_SLACK"

    if pd.notna(receive_slack) and receive_slack <= 0:
        return "LOW_RECEIVE_SLACK"

    # Receiver internal predecode side
    if (
        pd.notna(predecode)
        and predecode > PREDECODE_DELAY_MS
        and (pd.isna(packet_span) or packet_span <= PACKET_SPAN_JITTER_MS)
        and (pd.isna(missing) or missing == 0)
    ):
        return "PREDECODE_DELAY"

    # Queue/decode side
    if pd.notna(fb_res) and fb_res > FRAME_BUFFER_DELAY_MS:
        return "FRAME_BUFFER_DELAY"

    if pd.notna(dq_res) and dq_res > DECODE_QUEUE_DELAY_MS:
        return "DECODE_QUEUE_DELAY"

    if pd.notna(decode_time) and decode_time > DECODE_TIME_DELAY_MS:
        return "DECODE_DELAY"

    if pd.notna(decode_slack) and decode_slack <= 0:
        return "LOW_DECODE_SLACK"

    if pd.notna(decode_slack) and decode_slack <= 10:
        return "LOW_DECODE_SLACK_MARGIN"

    # If max_wait is negative but no single stage is obviously large
    if pd.notna(max_wait) and max_wait <= LATE_THRESHOLD_MS:
        return "PLAYOUT_BUDGET_EXHAUSTED"

    return "UNKNOWN"

def write_cause_explanations(f):
    f.write("[primary cause explanation]\n")
    f.write("---------------------------\n")
    f.write(
        "PACKET_LOSS_OR_REORDER\n"
        "  missing_seq_count > 0.\n"
        "  해당 frame을 구성하는 RTP sequence number 범위 안에 빠진 번호가 있다.\n"
        "  packet loss, packet reordering, or packet telemetry/capture 누락 가능성 존재.\n\n"
    )

    f.write(
        "PACKET_SPAN_JITTER\n"
        f"  packet_span_ms > {PACKET_SPAN_JITTER_MS} ms.\n"
        "  같은 frame에 속한 packet들이 한 번에 도착하지 않고 긴 시간 간격에 걸쳐 도착했다.\n"
        "  network jitter, sender pacing delay, SFU forwarding delay, or receiver-side packet arrival burstiness.\n\n"
    )

    f.write(
        "RECEIVE_AFTER_RENDER_TIME\n"
        "  renderMinusReceiveMs <= 0.\n"
        "  frame이 목표 render time과 같거나 그 이후에 receiver에 도착했다.\n"
        "  The frame was already too late when it arrived at the receiver.\n\n"
    )

    f.write(
        "LOW_RECEIVE_TO_RENDER_SLACK\n"
        "  0 < renderMinusReceiveMs <= 15 ms.\n"
        "  frame이 목표 render time 전에 도착하기는 했지만, 남은 시간 여유가 매우 작다.\n"
        "  Even small receiver-side processing delays can make this frame late.\n\n"
    )

    f.write(
        "LOW_RECEIVE_SLACK\n"
        "  receiveSlackMs <= 0.\n"
        "  receive 기준 slack budget이 이미 0 이하로 소진된 상태.\n\n"
    )

    f.write(
        "PREDECODE_DELAY\n"
        f"  preDecodeWaitingMs > {PREDECODE_DELAY_MS} ms while packet_span_ms is not large.\n"
        "  packet들은 충분히 빨리 도착했지만, frame이 FrameBuffer에 insert되기 전까지 오래 대기했다.\n\n"
    )

    f.write(
        "FRAME_BUFFER_DELAY\n"
        f"  frameBufferResidenceMs > {FRAME_BUFFER_DELAY_MS} ms.\n"
        "  The frame stayed too long inside FrameBuffer after insertion.\n"
        "  This can indicate waiting for decodability, reference dependency, timing decision, or scheduling delay.\n\n"
    )

    f.write(
        "DECODE_QUEUE_DELAY\n"
        f"  decodeQueueResidenceMs > {DECODE_QUEUE_DELAY_MS} ms.\n"
        "  The frame stayed too long in the decode queue before being extracted for decode.\n"
        "  This suggests decoder queue congestion or scheduling delay.\n\n"
    )

    f.write(
        "DECODE_DELAY\n"
        f"  decodeTimeMs > {DECODE_TIME_DELAY_MS} ms.\n"
        "  decodeTimeMs = decodeFinishMs - decodeStartMs.\n"
        "  The decoder itself took a long time to process the frame.\n\n"
    )

    f.write(
        "LOW_DECODE_SLACK\n"
        "  decodeSlackNominalMs <= 0.\n"
        "  The nominal decode slack is already exhausted.\n"
        "  The frame has no remaining decode budget under the nominal schedule.\n\n"
    )

    f.write(
        "LOW_DECODE_SLACK_MARGIN\n"
        "  0 < decodeSlackNominalMs <= 10 ms.\n"
        "  The frame has only a very small decode slack margin.\n"
        "  Minor queueing or decode delay can make it late.\n\n"
    )

    f.write(
        "PLAYOUT_BUDGET_EXHAUSTED\n"
        f"  max_wait <= {LATE_THRESHOLD_MS} ms, but no single earlier stage exceeded its threshold.\n"
        "  특정 한 단계가 크게 튄 것이 아니라 전체 playout/decode budget이 이미 소진된 상태로 예상된다.\n"
        "  This is a fallback category for negative max_wait when packet/predecode/queue/decode delays\n"
        "  are not individually large enough to explain the lateness.\n\n"
    )

    f.write(
        "UNKNOWN\n"
        "  No configured rule matched this frame.\n"
        "  This usually means more telemetry fields or better thresholds are needed.\n\n"
    )


def write_flag_explanations(f):
    f.write("[cause flag explanation]\n")
    f.write("------------------------\n")
    f.write(
        "causeFlags is a multi-label field.\n"
        "Unlike primaryCause, which selects one main reason, causeFlags records all suspicious conditions\n"
        "that are true for the frame. A frame may have multiple flags separated by '|'.\n\n"
    )

    f.write(
        "NEGATIVE_MAX_WAIT\n"
        f"  max_wait <= {LATE_THRESHOLD_MS} ms.\n"
        "  The frame is already late according to FrameDecodeTiming.\n\n"
    )

    f.write(
        "PACKET_LOSS_OR_REORDER\n"
        "  missing_seq_count > 0.\n"
        "  The frame has missing RTP sequence numbers.\n\n"
    )

    f.write(
        "PACKET_SPAN_JITTER\n"
        f"  packet_span_ms > {PACKET_SPAN_JITTER_MS} ms.\n"
        "  Packets of the same frame arrived over a long interval.\n\n"
    )

    f.write(
        "PREDECODE_DELAY\n"
        f"  preDecodeWaitingMs > {PREDECODE_DELAY_MS} ms.\n"
        "  The frame waited too long between receiveTimeMs and frameBufferInsertTimeMs.\n\n"
    )

    f.write(
        "FRAME_BUFFER_DELAY\n"
        f"  frameBufferResidenceMs > {FRAME_BUFFER_DELAY_MS} ms.\n"
        "  The frame stayed too long in FrameBuffer.\n\n"
    )

    f.write(
        "DECODE_QUEUE_DELAY\n"
        f"  decodeQueueResidenceMs > {DECODE_QUEUE_DELAY_MS} ms.\n"
        "  The frame stayed too long in the decode queue.\n\n"
    )

    f.write(
        "DECODE_DELAY\n"
        f"  decodeTimeMs > {DECODE_TIME_DELAY_MS} ms.\n"
        "  The actual decode execution took too long.\n\n"
    )

    f.write(
        "LOW_RECEIVE_TO_RENDER_SLACK\n"
        "  renderMinusReceiveMs <= 15 ms.\n"
        "  There was little time left between frame receive and target render time.\n\n"
    )

    f.write(
        "LOW_RECEIVE_SLACK\n"
        "  receiveSlackMs <= 0.\n"
        "  Receive slack budget was exhausted.\n\n"
    )

    f.write(
        "LOW_DECODE_SLACK\n"
        "  decodeSlackNominalMs <= 10 ms.\n"
        "  Decode slack budget was very small or already negative.\n\n"
    )

    f.write(
        "NONE\n"
        "  No suspicious flag condition was triggered.\n\n"
    )

def cause_flags(row):
    flags = []

    missing = row.get("missing_seq_count", 0)
    packet_span = row.get("packet_span_ms", np.nan)
    predecode = row.get("preDecodeWaitingMs", np.nan)
    fb_res = row.get("frameBufferResidenceMs", np.nan)
    dq_res = row.get("decodeQueueResidenceMs", np.nan)
    decode_time = row.get("decodeTimeMs", np.nan)
    render_minus_receive = row.get("renderMinusReceiveMs", np.nan)
    decode_slack = row.get("decodeSlackNominalMs", np.nan)
    max_wait = row.get("max_wait", np.nan)

    if pd.notna(missing) and missing > 0:
        flags.append("PACKET_LOSS_OR_REORDER")

    if pd.notna(packet_span) and packet_span > PACKET_SPAN_JITTER_MS:
        flags.append("PACKET_SPAN_JITTER")

    if pd.notna(predecode) and predecode > PREDECODE_DELAY_MS:
        flags.append("PREDECODE_DELAY")

    if pd.notna(fb_res) and fb_res > FRAME_BUFFER_DELAY_MS:
        flags.append("FRAME_BUFFER_DELAY")

    if pd.notna(dq_res) and dq_res > DECODE_QUEUE_DELAY_MS:
        flags.append("DECODE_QUEUE_DELAY")

    if pd.notna(decode_time) and decode_time > DECODE_TIME_DELAY_MS:
        flags.append("DECODE_DELAY")

    if pd.notna(render_minus_receive) and render_minus_receive <= 15:
        flags.append("LOW_RECEIVE_TO_RENDER_SLACK")

    if pd.notna(decode_slack) and decode_slack <= 10:
        flags.append("LOW_DECODE_SLACK")

    if pd.notna(max_wait) and max_wait <= LATE_THRESHOLD_MS:
        flags.append("NEGATIVE_MAX_WAIT")

    if not flags:
        return "NONE"

    return "|".join(flags)

def main():
    records = clean_columns(pd.read_csv(FRAME_RECORDS_CSV, skipinitialspace=True))
    packets = clean_columns(pd.read_csv(FRAME_PACKETS_CSV, skipinitialspace=True))

    numeric_record_cols = [
        "frameId",
        "receiveTimeMs",
        "render_time",
        "max_wait",
        "frameBufferInsertTimeMs",
        "frameBufferExtractTimeMs",
        "decodeQueueInsertTimeMs",
        "decodeQueueExtractTimeMs",
        "decodeStartMs",
        "decodeFinishMs",
        "frameBufferResidenceMs",
        "decodeQueueResidenceMs",
        "decodeSlackNominalMs",
        "receiveSlackMs",
        "currentSpatialLayer",
        "availableBitrateBps",
    ]

    numeric_packet_cols = [
        "frameId",
        "sequenceNumber",
        "receiveTimeMs",
    ]

    records = to_num(records, numeric_record_cols)
    packets = to_num(packets, numeric_packet_cols)

    packet_agg = (
        packets.groupby("frameId")
        .agg(
            packet_count=("sequenceNumber", "count"),
            first_seq=("sequenceNumber", "min"),
            last_seq=("sequenceNumber", "max"),
            first_packet_receive_ms=("receiveTimeMs", "min"),
            last_packet_receive_ms=("receiveTimeMs", "max"),
        )
        .reset_index()
    )

    packet_agg["packet_span_ms"] = (
        packet_agg["last_packet_receive_ms"]
        - packet_agg["first_packet_receive_ms"]
    )

    packet_agg["seq_span"] = (
        packet_agg["last_seq"] - packet_agg["first_seq"] + 1
    )

    packet_agg["missing_seq_count"] = (
        packet_agg["seq_span"] - packet_agg["packet_count"]
    )

    df = records.merge(packet_agg, on="frameId", how="left")

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

    df["preDecodeWaitingMs"] = np.nan
    valid_predecode = (
        df["receiveTimeMs"].notna()
        & (df["receiveTimeMs"] > 0)
        & df["frameBufferInsertTimeMs"].notna()
        & (df["frameBufferInsertTimeMs"] > 0)
    )
    df.loc[valid_predecode, "preDecodeWaitingMs"] = (
        df.loc[valid_predecode, "frameBufferInsertTimeMs"]
        - df.loc[valid_predecode, "receiveTimeMs"]
    )
    df.loc[df["preDecodeWaitingMs"] < 0, "preDecodeWaitingMs"] = 0

    df["decodeTimeMs"] = np.nan
    valid_decode = (
        df["decodeStartMs"].notna()
        & df["decodeFinishMs"].notna()
        & (df["decodeStartMs"] > 0)
        & (df["decodeFinishMs"] > 0)
    )
    df.loc[valid_decode, "decodeTimeMs"] = (
        df.loc[valid_decode, "decodeFinishMs"]
        - df.loc[valid_decode, "decodeStartMs"]
    )

    df["isDropped"] = classify_dropped(df)

    df["frameClass"] = "SAFE_NORMAL"
    df.loc[
        (~df["isDropped"]) & (df["max_wait"] <= LATE_THRESHOLD_MS),
        "frameClass",
    ] = "LATE_NORMAL"
    df.loc[df["isDropped"], "frameClass"] = "DROPPED"

    target = df[df["frameClass"].isin(["LATE_NORMAL", "DROPPED"])].copy()
    target["primaryCause"] = target.apply(primary_cause, axis=1)
    target["causeFlags"] = target.apply(cause_flags, axis=1)

    out_cols = [
        "frameId",
        "frameClass",
        "primaryCause",
        "max_wait",
        "receiveTimeMs",
        "render_time",
        "preDecodeWaitingMs",
        "packet_span_ms",
        "packet_count",
        "first_seq",
        "last_seq",
        "missing_seq_count",
        "frameBufferInsertTimeMs",
        "frameBufferExtractTimeMs",
        "frameBufferResidenceMs",
        "decodeQueueInsertTimeMs",
        "decodeQueueExtractTimeMs",
        "decodeQueueResidenceMs",
        "decodeStartMs",
        "decodeFinishMs",
        "decodeTimeMs",
        "currentSpatialLayer",
        "availableBitrateBps",
        "renderMinusReceiveMs",
        "receiveSlackMs",
        "decodeSlackNominalMs",
        "causeFlags",
    ]

    out_cols = [c for c in out_cols if c in target.columns]

    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    target[out_cols].to_csv(OUT_CSV, index=False)

    with open(OUT_TXT, "w") as f:
        f.write("Late/drop cause analysis\n")
        f.write("========================\n\n")
        f.write(f"input records: {FRAME_RECORDS_CSV}\n")
        f.write(f"input packets: {FRAME_PACKETS_CSV}\n")
        f.write(f"late threshold ms: {LATE_THRESHOLD_MS}\n\n")

        write_cause_explanations(f)
        f.write("\n")

        write_flag_explanations(f)
        f.write("\n")

        f.write("[frame class counts]\n")
        f.write(str(df["frameClass"].value_counts()) + "\n\n")

        f.write("[primary cause counts among late/drop]\n")
        f.write(str(target["primaryCause"].value_counts()) + "\n\n")

        f.write("[summary by frameClass]\n")
        metrics = [
            "max_wait",
            "preDecodeWaitingMs",
            "packet_span_ms",
            "missing_seq_count",
            "frameBufferResidenceMs",
            "decodeQueueResidenceMs",
            "decodeTimeMs",
            "renderMinusReceiveMs",
            "receiveSlackMs",
            "decodeSlackNominalMs",
        ]
        for cls in ["LATE_NORMAL", "DROPPED"]:
            f.write(f"\n[{cls}]\n")
            f.write(str(target[target["frameClass"] == cls][metrics].describe()) + "\n")

        f.write("\n[worst frames]\n")
        worst_cols = [
            "frameId",
            "frameClass",
            "primaryCause",
            "max_wait",
            "preDecodeWaitingMs",
            "packet_span_ms",
            "missing_seq_count",
            "packet_count",
        ]
        f.write(
            target.sort_values(["frameClass", "max_wait"])
            [worst_cols]
            .to_string(index=False)
        )
        f.write("\n")

    print(f"[INFO] saved: {OUT_CSV}")
    print(f"[INFO] saved: {OUT_TXT}")
    print("\n[INFO] primary cause counts:")
    print(target["primaryCause"].value_counts())


if __name__ == "__main__":
    main()
