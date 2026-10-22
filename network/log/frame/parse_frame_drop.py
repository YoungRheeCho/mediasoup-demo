#!/usr/bin/env python3

## PARSE AND ANALYZE FRAME DROP

import csv
import re
from pathlib import Path
from typing import Any


INPUT_LOG = (
    "/home/n2sl/yeon/qos/network/log/frame/log/telemetry_all.log"
)

OUTPUT_CSV = "csv/frame_drop_analysis.csv"


RTP_CLOCK_HZ = 90_000

# 자동 분류를 위한 보조 기준.
LARGE_PACKET_SPAN_MS = 50.0
LARGE_REFERENCE_FINDER_WAIT_MS = 20.0

# SSRC가 로그에 없으므로 keyframe request는 같은 스트림인지 확정할 수 없다.
# 로그 줄 기준으로 가까운 요청인지만 표시한다.
NEAR_KEYFRAME_REQUEST_MAX_LINES = 300


FRAME_DROP_RE = re.compile(
    r"\[FRAME DROP\]\s*(?P<body>.*)$"
)

ALREADY_DECODED_CHECK_RE = re.compile(
    r"\[FRAME ALREADY DECODED CHECK\]\s*(?P<body>.*)$"
)

ALREADY_DECODED_REFERENCE_RE = re.compile(
    r"\[FRAME ALREADY DECODED REFERENCE\]\s*(?P<body>.*)$"
)

FRAME_COMPLETE_RE = re.compile(
    r"\[FRAME COMPLETE\]\s*(?P<body>.*)$"
)

FRAME_EXTRACT_RE = re.compile(
    r"\[FRAME EXTRACT\]\s*(?P<body>.*)$"
)

KEYFRAME_REQUEST_RE = re.compile(
    r"\[KEYFRAME REQUEST\]\s*(?P<body>.*)$"
)

KEYFRAME_REQUEST_SENT_RE = re.compile(
    r"\[KEYFRAME REQUEST SENT\]\s*(?P<body>.*)$"
)

KV_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)"
)


FIELDNAMES = [
    # ============================================================
    # 기본 FRAME DROP 정보
    # ============================================================
    "reason",
    "frame_id",
    "rtp_timestamp",
    "drop_line_no",

    "last_decoded_frame_id",
    "frame_id_delta",

    "frame_rtp_timestamp",
    "last_decoded_rtp_timestamp",
    "has_last_decoded_rtp_timestamp",
    "frame_rtp_is_newer_than_last_decoded",

    "rtp_gap_ticks",
    "rtp_gap_ms",

    "is_keyframe",
    "legacy_frame_id_jump_behavior",
    "num_references",
    "reference_frame_ids",

    # ============================================================
    # 드랍된 프레임의 FRAME COMPLETE 정보
    # C++ [FRAME COMPLETE] 로그에서 수집
    # ============================================================
    "dropped_complete_found",
    "dropped_complete_line_no",

    "dropped_complete_rtp_timestamp",

    # 실제 codec frame type
    "dropped_complete_frame_type",
    "dropped_complete_actual_codec_keyframe",

    # ReferenceFinder 기준
    "dropped_complete_reference_is_keyframe",
    "dropped_complete_num_references",
    "dropped_complete_reference0",

    "dropped_complete_first_seq_num",
    "dropped_complete_last_seq_num",

    "dropped_complete_times_nacked",
    "dropped_complete_delayed_by_retransmission",

    "dropped_complete_receive_start_ms",
    "dropped_complete_receive_finish_ms",
    "dropped_complete_packet_span_ms",

    "dropped_complete_callback_ms",
    "dropped_complete_reference_finder_wait_ms",

    "dropped_complete_frame_size_bytes",

    # ============================================================
    # last_decoded_frame의 FRAME COMPLETE 정보
    # ============================================================
    "last_complete_found",
    "last_complete_line_no",

    "last_complete_rtp_timestamp",

    "last_complete_frame_type",
    "last_complete_actual_codec_keyframe",

    "last_complete_reference_is_keyframe",
    "last_complete_num_references",
    "last_complete_reference0",

    "last_complete_first_seq_num",
    "last_complete_last_seq_num",

    "last_complete_times_nacked",
    "last_complete_delayed_by_retransmission",

    "last_complete_receive_start_ms",
    "last_complete_receive_finish_ms",
    "last_complete_packet_span_ms",

    "last_complete_callback_ms",
    "last_complete_reference_finder_wait_ms",

    "last_complete_frame_size_bytes",

    # ============================================================
    # last_decoded_frame의 FRAME EXTRACT 정보
    # ============================================================
    "last_extract_found",
    "last_extract_line_no",

    "last_extract_rtp_timestamp",

    "last_extract_frame_type",
    "last_extract_actual_codec_keyframe",

    "last_extract_reference_is_keyframe",
    "last_extract_num_references",
    "last_extract_reference0",

    "last_extract_delayed_by_retransmission",

    "last_extract_receive_start_ms",
    "last_extract_receive_finish_ms",
    "last_extract_packet_span_ms",

    "last_extract_frame_size_bytes",
    "last_extract_time_ms",

    # ============================================================
    # 프레임 순서 및 원인 분석
    # ============================================================
    "dropped_complete_after_last_extract",
    "complete_extract_line_gap",

    # 드랍 프레임이 다음 프레임보다 실제로 늦게 완성됐는지
    "dropped_finish_minus_last_finish_ms",

    # 실제 codec 기준으로 두 프레임이 모두 keyframe인지
    "both_frames_actual_codec_keyframes",

    # ReferenceFinder 기준으로 두 프레임이 모두 독립 프레임인지
    "both_frames_independently_decodable",

    # actual codec frame type과 ReferenceFinder 판단이 불일치하는지
    "dropped_codec_reference_disagreement",
    "last_codec_reference_disagreement",

    "retransmission_evidence",
    "large_packet_span_evidence",
    "reference_finder_delay_evidence",

    # ============================================================
    # Keyframe request
    # ============================================================
    "keyframe_request_found",
    "keyframe_request_line_no",
    "keyframe_request_line_distance",

    "keyframe_request_reason",
    "keyframe_request_wait_ms",
    "keyframe_request_last_received_rtp_timestamp",
    "keyframe_request_last_decoded_rtp_timestamp",

    "keyframe_request_sent_found",
    "keyframe_request_sent_line_no",
    "keyframe_request_sent_line_distance",
    "keyframe_request_time_ms",

    "request_to_dropped_complete_ms",

    # SSRC 확인 없이 로그 거리로만 판단한 값
    "nearby_keyframe_request_unverified",

    # ============================================================
    # 최종 자동 분석
    # ============================================================
    "likely_cause",
    "evidence_summary",

    # ============================================================
    # 다른 드랍 로그와의 호환 필드
    # ============================================================
    "buffer_size",
    "max_size",

    "last_rtp_timestamp",
    "now",
    "render_time",
    "max_wait",
    "target_delay",
    "current_delay",
    "jitter_delay",
    "minimum_delay",
    "min_playout_delay",
    "max_playout_delay",
    "render_delay",
    "decode_time",
    "num_decoded_frames",
]


FIELDNAME_SET = set(FIELDNAMES)


def extract_pairs(body: str) -> dict[str, str]:
    """로그 본문에서 key=value 쌍을 추출한다."""
    return dict(KV_RE.findall(body))


def first_value(
    pairs: dict[str, Any] | None,
    *keys: str,
) -> Any:
    """후보 키 중 값이 존재하는 첫 번째 항목을 반환한다."""
    if not pairs:
        return ""

    for key in keys:
        value = pairs.get(key)

        if value is not None and value != "":
            return value

    return ""


def parse_int(value: Any) -> int | None:
    if value is None or value == "":
        return None

    if isinstance(value, bool):
        return int(value)

    if isinstance(value, int):
        return value

    text = str(value).strip().lower()

    if text == "true":
        return 1

    if text == "false":
        return 0

    try:
        return int(text)
    except ValueError:
        return None


def parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_bool(value: Any) -> bool | None:
    """
    true/false, 1/0을 bool로 변환한다.

    값이 없거나 변환할 수 없으면 None.
    """
    if value is None or value == "":
        return None

    if isinstance(value, bool):
        return value

    text = str(value).strip().lower()

    if text in {"1", "true"}:
        return True

    if text in {"0", "false"}:
        return False

    return None


def bool_to_csv(value: bool | None) -> int | str:
    if value is None:
        return ""

    return int(value)


def normalize_drop_fields(pairs: dict[str, Any]) -> None:
    """
    기존 FRAME DROP 로그와 새 로그의 필드명을 호환시킨다.
    """

    if (
        "rtp_timestamp" not in pairs
        and "frame_rtp_timestamp" in pairs
    ):
        pairs["rtp_timestamp"] = pairs["frame_rtp_timestamp"]

    if (
        "frame_rtp_timestamp" not in pairs
        and "rtp_timestamp" in pairs
    ):
        pairs["frame_rtp_timestamp"] = pairs["rtp_timestamp"]

    if (
        "last_rtp_timestamp" not in pairs
        and "last_decoded_rtp_timestamp" in pairs
    ):
        pairs["last_rtp_timestamp"] = (
            pairs["last_decoded_rtp_timestamp"]
        )


def signed_rtp_difference(
    newer_timestamp: int,
    older_timestamp: int,
) -> int:
    """
    uint32 wraparound을 고려한 RTP timestamp 차이.

    반환:
        newer_timestamp - older_timestamp
    """

    difference = (
        newer_timestamp - older_timestamp
    ) & 0xFFFFFFFF

    if difference & 0x80000000:
        difference -= 0x100000000

    return difference


def append_frame_event(
    storage: dict[str, list[dict[str, Any]]],
    frame_id: str | None,
    event: dict[str, Any],
) -> None:
    if frame_id is None:
        return

    storage.setdefault(frame_id, []).append(event)


def latest_frame_event(
    storage: dict[str, list[dict[str, Any]]],
    frame_id: str | None,
) -> dict[str, Any] | None:
    if frame_id is None:
        return None

    events = storage.get(frame_id)

    if not events:
        return None

    return events[-1]


def copy_drop_fields(
    row: dict[str, Any],
    pairs: dict[str, Any],
) -> None:
    normalize_drop_fields(pairs)

    for key, value in pairs.items():
        if key in FIELDNAME_SET:
            row[key] = value


def copy_complete_event(
    row: dict[str, Any],
    event: dict[str, Any] | None,
    prefix: str,
) -> None:
    """
    [FRAME COMPLETE] 로그를 CSV 컬럼에 복사한다.

    prefix:
        dropped_complete
        last_complete
    """

    if prefix not in {
        "dropped_complete",
        "last_complete",
    }:
        raise ValueError(
            f"Unsupported complete prefix: {prefix}"
        )

    found_key = f"{prefix}_found"
    line_key = f"{prefix}_line_no"

    if event is None:
        row[found_key] = 0
        return

    row[found_key] = 1
    row[line_key] = event["line_no"]

    pairs = event["pairs"]

    row[f"{prefix}_rtp_timestamp"] = first_value(
        pairs,
        "rtp_timestamp",
        "frame_rtp_timestamp",
    )

    # 마지막 C++ 로그:
    # frame_type=...
    row[f"{prefix}_frame_type"] = first_value(
        pairs,
        "frame_type",
        "rtp_frame_type",
    )

    # 마지막 C++ 로그:
    # actual_codec_keyframe=...
    row[f"{prefix}_actual_codec_keyframe"] = first_value(
        pairs,
        "actual_codec_keyframe",
        "rtp_header_is_keyframe",
    )

    # 마지막 C++ 로그:
    # is_keyframe=...
    # EncodedFrame::is_keyframe(), 즉 num_references == 0 기준
    row[f"{prefix}_reference_is_keyframe"] = first_value(
        pairs,
        "is_keyframe",
        "reference_is_keyframe",
    )

    row[f"{prefix}_num_references"] = first_value(
        pairs,
        "num_references",
    )

    row[f"{prefix}_reference0"] = first_value(
        pairs,
        "reference0",
    )

    row[f"{prefix}_first_seq_num"] = first_value(
        pairs,
        "first_seq_num",
    )

    row[f"{prefix}_last_seq_num"] = first_value(
        pairs,
        "last_seq_num",
    )

    row[f"{prefix}_times_nacked"] = first_value(
        pairs,
        "times_nacked",
    )

    row[
        f"{prefix}_delayed_by_retransmission"
    ] = first_value(
        pairs,
        "delayed_by_retransmission",
    )

    row[f"{prefix}_receive_start_ms"] = first_value(
        pairs,
        "receive_start_ms",
    )

    row[f"{prefix}_receive_finish_ms"] = first_value(
        pairs,
        "receive_finish_ms",
        "ReceivedTime",
    )

    row[f"{prefix}_packet_span_ms"] = first_value(
        pairs,
        "packet_span_ms",
    )

    row[f"{prefix}_callback_ms"] = first_value(
        pairs,
        "complete_callback_ms",
    )

    row[
        f"{prefix}_reference_finder_wait_ms"
    ] = first_value(
        pairs,
        "reference_finder_wait_ms",
    )

    row[f"{prefix}_frame_size_bytes"] = first_value(
        pairs,
        "frame_size_bytes",
    )

    # C++에서 packet_span_ms를 직접 출력하지 못한 경우 계산.
    if row[f"{prefix}_packet_span_ms"] == "":
        receive_start = parse_float(
            row[f"{prefix}_receive_start_ms"]
        )
        receive_finish = parse_float(
            row[f"{prefix}_receive_finish_ms"]
        )

        if (
            receive_start is not None
            and receive_finish is not None
            and receive_finish >= receive_start
        ):
            row[f"{prefix}_packet_span_ms"] = (
                receive_finish - receive_start
            )

    # C++에서 reference_finder_wait_ms를 직접 출력하지 못한 경우 계산.
    if (
        row[f"{prefix}_reference_finder_wait_ms"]
        == ""
    ):
        callback_ms = parse_float(
            row[f"{prefix}_callback_ms"]
        )
        receive_finish = parse_float(
            row[f"{prefix}_receive_finish_ms"]
        )

        if (
            callback_ms is not None
            and receive_finish is not None
        ):
            row[
                f"{prefix}_reference_finder_wait_ms"
            ] = callback_ms - receive_finish


def copy_extract_event(
    row: dict[str, Any],
    event: dict[str, Any] | None,
) -> None:
    """
    [FRAME EXTRACT] 로그를 CSV 컬럼에 복사한다.

    FRAME EXTRACT 로그가 이전 필드명을 사용해도 처리한다.
    """

    prefix = "last_extract"

    if event is None:
        row["last_extract_found"] = 0
        return

    row["last_extract_found"] = 1
    row["last_extract_line_no"] = event["line_no"]

    pairs = event["pairs"]

    row[f"{prefix}_rtp_timestamp"] = first_value(
        pairs,
        "rtp_timestamp",
        "frame_rtp_timestamp",
    )

    row[f"{prefix}_frame_type"] = first_value(
        pairs,
        "frame_type",
        "rtp_frame_type",
    )

    row[f"{prefix}_actual_codec_keyframe"] = first_value(
        pairs,
        "actual_codec_keyframe",
        "rtp_header_is_keyframe",
    )

    row[f"{prefix}_reference_is_keyframe"] = first_value(
        pairs,
        "is_keyframe",
        "reference_is_keyframe",
    )

    row[f"{prefix}_num_references"] = first_value(
        pairs,
        "num_references",
    )

    row[f"{prefix}_reference0"] = first_value(
        pairs,
        "reference0",
    )

    row[
        f"{prefix}_delayed_by_retransmission"
    ] = first_value(
        pairs,
        "delayed_by_retransmission",
    )

    row[f"{prefix}_receive_start_ms"] = first_value(
        pairs,
        "receive_start_ms",
    )

    row[f"{prefix}_receive_finish_ms"] = first_value(
        pairs,
        "receive_finish_ms",
        "ReceivedTime",
    )

    row[f"{prefix}_packet_span_ms"] = first_value(
        pairs,
        "packet_span_ms",
    )

    row[f"{prefix}_frame_size_bytes"] = first_value(
        pairs,
        "frame_size_bytes",
    )

    row["last_extract_time_ms"] = first_value(
        pairs,
        "extract_time_ms",
    )

    if row[f"{prefix}_packet_span_ms"] == "":
        receive_start = parse_float(
            row[f"{prefix}_receive_start_ms"]
        )
        receive_finish = parse_float(
            row[f"{prefix}_receive_finish_ms"]
        )

        if (
            receive_start is not None
            and receive_finish is not None
            and receive_finish >= receive_start
        ):
            row[f"{prefix}_packet_span_ms"] = (
                receive_finish - receive_start
            )


def nearest_previous_event(
    events: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not events:
        return None

    return events[-1]


def add_keyframe_request_fields(
    row: dict[str, Any],
    drop_line_no: int,
    request_events: list[dict[str, Any]],
    sent_events: list[dict[str, Any]],
) -> None:
    request_event = nearest_previous_event(
        request_events
    )

    if request_event is None:
        row["keyframe_request_found"] = 0
    else:
        request_pairs = request_event["pairs"]
        request_line_no = request_event["line_no"]

        row["keyframe_request_found"] = 1
        row["keyframe_request_line_no"] = (
            request_line_no
        )
        row["keyframe_request_line_distance"] = (
            drop_line_no - request_line_no
        )

        row["keyframe_request_reason"] = first_value(
            request_pairs,
            "reason",
        )

        row["keyframe_request_wait_ms"] = first_value(
            request_pairs,
            "wait_ms",
        )

        row[
            "keyframe_request_last_received_rtp_timestamp"
        ] = first_value(
            request_pairs,
            "last_received_rtp_timestamp",
        )

        row[
            "keyframe_request_last_decoded_rtp_timestamp"
        ] = first_value(
            request_pairs,
            "last_decoded_rtp_timestamp",
        )

    sent_event = nearest_previous_event(
        sent_events
    )

    if sent_event is None:
        row["keyframe_request_sent_found"] = 0
    else:
        sent_pairs = sent_event["pairs"]
        sent_line_no = sent_event["line_no"]

        row["keyframe_request_sent_found"] = 1
        row["keyframe_request_sent_line_no"] = (
            sent_line_no
        )
        row[
            "keyframe_request_sent_line_distance"
        ] = (
            drop_line_no - sent_line_no
        )

        row["keyframe_request_time_ms"] = first_value(
            sent_pairs,
            "request_time_ms",
        )


def add_derived_analysis(
    row: dict[str, Any],
) -> None:
    evidence: list[str] = []

    reason = str(row.get("reason", ""))

    # ============================================================
    # RTP timestamp 차이
    # ============================================================
    frame_rtp_timestamp = parse_int(
        row.get("frame_rtp_timestamp")
        or row.get("rtp_timestamp")
    )

    last_decoded_rtp_timestamp = parse_int(
        row.get("last_decoded_rtp_timestamp")
    )

    if (
        frame_rtp_timestamp is not None
        and last_decoded_rtp_timestamp is not None
    ):
        rtp_gap_ticks = signed_rtp_difference(
            last_decoded_rtp_timestamp,
            frame_rtp_timestamp,
        )

        row["rtp_gap_ticks"] = rtp_gap_ticks
        row["rtp_gap_ms"] = (
            rtp_gap_ticks * 1000.0
            / RTP_CLOCK_HZ
        )

    # ============================================================
    # 로그 이벤트 순서
    # ============================================================
    dropped_complete_line = parse_int(
        row.get("dropped_complete_line_no")
    )

    last_extract_line = parse_int(
        row.get("last_extract_line_no")
    )

    if (
        dropped_complete_line is not None
        and last_extract_line is not None
    ):
        line_gap = (
            dropped_complete_line
            - last_extract_line
        )

        completed_after_extract = line_gap > 0

        row["complete_extract_line_gap"] = line_gap
        row[
            "dropped_complete_after_last_extract"
        ] = int(completed_after_extract)

        if completed_after_extract:
            evidence.append(
                "older_frame_completed_after_newer_extract"
            )
    else:
        completed_after_extract = False

    # ============================================================
    # 실제 프레임 완성 시각 비교
    # ============================================================
    dropped_finish = parse_float(
        row.get(
            "dropped_complete_receive_finish_ms"
        )
    )

    last_finish = parse_float(
        row.get(
            "last_complete_receive_finish_ms"
        )
    )

    if (
        dropped_finish is not None
        and last_finish is not None
    ):
        finish_delta = (
            dropped_finish - last_finish
        )

        row[
            "dropped_finish_minus_last_finish_ms"
        ] = finish_delta

        if finish_delta > 0:
            evidence.append(
                "older_frame_receive_finish_was_later"
            )

    # ============================================================
    # Codec keyframe 여부
    # ============================================================
    dropped_actual_keyframe = parse_bool(
        row.get(
            "dropped_complete_actual_codec_keyframe"
        )
    )

    last_actual_keyframe = parse_bool(
        row.get(
            "last_complete_actual_codec_keyframe"
        )
    )

    if last_actual_keyframe is None:
        last_actual_keyframe = parse_bool(
            row.get(
                "last_extract_actual_codec_keyframe"
            )
        )

    both_actual_keyframes: bool | None

    if (
        dropped_actual_keyframe is None
        or last_actual_keyframe is None
    ):
        both_actual_keyframes = None
    else:
        both_actual_keyframes = (
            dropped_actual_keyframe
            and last_actual_keyframe
        )

    row[
        "both_frames_actual_codec_keyframes"
    ] = bool_to_csv(both_actual_keyframes)

    if both_actual_keyframes:
        evidence.append(
            "both_frames_are_actual_codec_keyframes"
        )

    # ============================================================
    # ReferenceFinder 기준 독립 프레임 여부
    # ============================================================
    dropped_reference_keyframe = parse_bool(
        row.get(
            "dropped_complete_reference_is_keyframe"
        )
    )

    if dropped_reference_keyframe is None:
        dropped_reference_keyframe = parse_bool(
            row.get("is_keyframe")
        )

    last_reference_keyframe = parse_bool(
        row.get(
            "last_extract_reference_is_keyframe"
        )
    )

    if last_reference_keyframe is None:
        last_reference_keyframe = parse_bool(
            row.get(
                "last_complete_reference_is_keyframe"
            )
        )

    both_reference_independent: bool | None

    if (
        dropped_reference_keyframe is None
        or last_reference_keyframe is None
    ):
        both_reference_independent = None
    else:
        both_reference_independent = (
            dropped_reference_keyframe
            and last_reference_keyframe
        )

    row[
        "both_frames_independently_decodable"
    ] = bool_to_csv(
        both_reference_independent
    )

    if both_reference_independent:
        evidence.append(
            "both_frames_have_zero_references"
        )

    # ============================================================
    # 실제 codec keyframe 여부와 reference 판단 불일치
    # ============================================================
    dropped_disagreement: bool | None

    if (
        dropped_actual_keyframe is None
        or dropped_reference_keyframe is None
    ):
        dropped_disagreement = None
    else:
        dropped_disagreement = (
            dropped_actual_keyframe
            != dropped_reference_keyframe
        )

    row[
        "dropped_codec_reference_disagreement"
    ] = bool_to_csv(dropped_disagreement)

    last_disagreement: bool | None

    if (
        last_actual_keyframe is None
        or last_reference_keyframe is None
    ):
        last_disagreement = None
    else:
        last_disagreement = (
            last_actual_keyframe
            != last_reference_keyframe
        )

    row[
        "last_codec_reference_disagreement"
    ] = bool_to_csv(last_disagreement)

    if dropped_disagreement:
        evidence.append(
            "dropped_codec_reference_disagreement"
        )

    if last_disagreement:
        evidence.append(
            "last_codec_reference_disagreement"
        )

    # ============================================================
    # NACK / retransmission
    # ============================================================
    times_nacked = parse_int(
        row.get(
            "dropped_complete_times_nacked"
        )
    )

    delayed_by_retransmission = parse_bool(
        row.get(
            "dropped_complete_delayed_by_retransmission"
        )
    )

    retransmission_evidence = (
        delayed_by_retransmission is True
        or (
            times_nacked is not None
            and times_nacked > 0
        )
    )

    row["retransmission_evidence"] = int(
        retransmission_evidence
    )

    if retransmission_evidence:
        evidence.append(
            "nack_or_retransmission"
        )

    # ============================================================
    # 프레임 내 packet span
    # ============================================================
    packet_span_ms = parse_float(
        row.get(
            "dropped_complete_packet_span_ms"
        )
    )

    large_packet_span = (
        packet_span_ms is not None
        and packet_span_ms
        >= LARGE_PACKET_SPAN_MS
    )

    row["large_packet_span_evidence"] = int(
        large_packet_span
    )

    if large_packet_span:
        evidence.append(
            "large_frame_packet_span"
        )

    # ============================================================
    # ReferenceFinder 내부 대기
    # ============================================================
    reference_finder_wait_ms = parse_float(
        row.get(
            "dropped_complete_reference_finder_wait_ms"
        )
    )

    reference_finder_delay = (
        reference_finder_wait_ms is not None
        and reference_finder_wait_ms
        >= LARGE_REFERENCE_FINDER_WAIT_MS
    )

    row[
        "reference_finder_delay_evidence"
    ] = int(reference_finder_delay)

    if reference_finder_delay:
        evidence.append(
            "large_reference_finder_wait"
        )

    # ============================================================
    # Keyframe request 근접 여부
    #
    # SSRC가 로그에 없기 때문에 원인 확정에는 사용하지 않는다.
    # ============================================================
    request_line_distance = parse_int(
        row.get(
            "keyframe_request_line_distance"
        )
    )

    sent_line_distance = parse_int(
        row.get(
            "keyframe_request_sent_line_distance"
        )
    )

    line_distances = [
        value
        for value in (
            request_line_distance,
            sent_line_distance,
        )
        if value is not None and value >= 0
    ]

    nearby_request = (
        bool(line_distances)
        and min(line_distances)
        <= NEAR_KEYFRAME_REQUEST_MAX_LINES
    )

    request_time_ms = parse_float(
        row.get("keyframe_request_time_ms")
    )

    dropped_callback_ms = parse_float(
        row.get(
            "dropped_complete_callback_ms"
        )
    )

    if (
        request_time_ms is not None
        and dropped_callback_ms is not None
    ):
        request_delta = (
            dropped_callback_ms
            - request_time_ms
        )

        row[
            "request_to_dropped_complete_ms"
        ] = request_delta

        if 0 <= request_delta <= 2000:
            nearby_request = True

    row[
        "nearby_keyframe_request_unverified"
    ] = int(nearby_request)

    if nearby_request:
        evidence.append(
            "nearby_keyframe_request_unverified_stream"
        )

    # ============================================================
    # 최종 원인 분류
    # ============================================================
    frame_rtp_is_newer = parse_bool(
        row.get(
            "frame_rtp_is_newer_than_last_decoded"
        )
    )

    dropped_complete_found = (
        parse_bool(
            row.get("dropped_complete_found")
        )
        is True
    )

    last_extract_found = (
        parse_bool(
            row.get("last_extract_found")
        )
        is True
    )

    if reason != "kAlreadyDecoded":
        likely_cause = reason or "unknown_drop_reason"

    elif frame_rtp_is_newer is True:
        likely_cause = (
            "frame_id_or_picture_id_discontinuity"
        )

    elif not dropped_complete_found:
        likely_cause = (
            "insufficient_data_missing_dropped_frame_complete"
        )

    elif not last_extract_found:
        likely_cause = (
            "insufficient_data_missing_last_decoded_extract"
        )

    elif dropped_disagreement or last_disagreement:
        likely_cause = (
            "codec_frame_type_and_reference_metadata_disagree"
        )

    elif completed_after_extract:
        if retransmission_evidence:
            likely_cause = (
                "older_frame_completed_after_newer_extract_"
                "because_of_nack_or_retransmission"
            )

        elif large_packet_span:
            likely_cause = (
                "older_frame_completed_after_newer_extract_"
                "because_of_large_packet_arrival_span"
            )

        elif reference_finder_delay:
            likely_cause = (
                "older_frame_completed_after_newer_extract_"
                "because_of_reference_finder_delay"
            )

        else:
            likely_cause = (
                "older_frame_completed_after_newer_extract_"
                "likely_packet_reordering_or_upstream_queue_delay"
            )

        if both_actual_keyframes:
            likely_cause += (
                "_newer_actual_keyframe_was_independently_decodable"
            )

        elif both_reference_independent:
            likely_cause += (
                "_both_frames_had_zero_references"
            )

    else:
        likely_cause = (
            "already_decoded_condition_detected_"
            "but_event_order_not_confirmed"
        )

    row["likely_cause"] = likely_cause
    row["evidence_summary"] = "|".join(evidence)


def validate_row(
    row: dict[str, Any],
    row_index: int,
) -> None:
    unknown_fields = (
        set(row.keys()) - FIELDNAME_SET
    )

    if unknown_fields:
        raise ValueError(
            f"Row {row_index} contains unknown fields: "
            f"{sorted(unknown_fields)}"
        )


def main() -> None:
    rows: list[dict[str, Any]] = []

    complete_events: dict[
        str,
        list[dict[str, Any]],
    ] = {}

    extract_events: dict[
        str,
        list[dict[str, Any]],
    ] = {}

    pending_checks: dict[
        str,
        dict[str, Any],
    ] = {}

    pending_references: dict[
        str,
        list[tuple[int, str]],
    ] = {}

    keyframe_request_events: list[
        dict[str, Any]
    ] = []

    keyframe_request_sent_events: list[
        dict[str, Any]
    ] = []

    with open(
        INPUT_LOG,
        "r",
        encoding="utf-8",
        errors="replace",
    ) as log_file:

        for line_no, line in enumerate(
            log_file,
            start=1,
        ):
            # --------------------------------------------------------
            # FRAME COMPLETE
            # --------------------------------------------------------
            match = FRAME_COMPLETE_RE.search(line)

            if match:
                pairs = extract_pairs(
                    match.group("body")
                )

                frame_id = pairs.get("frame_id")

                append_frame_event(
                    complete_events,
                    frame_id,
                    {
                        "line_no": line_no,
                        "pairs": pairs,
                    },
                )

                continue

            # --------------------------------------------------------
            # FRAME EXTRACT
            # --------------------------------------------------------
            match = FRAME_EXTRACT_RE.search(line)

            if match:
                pairs = extract_pairs(
                    match.group("body")
                )

                frame_id = pairs.get("frame_id")

                append_frame_event(
                    extract_events,
                    frame_id,
                    {
                        "line_no": line_no,
                        "pairs": pairs,
                    },
                )

                continue

            # --------------------------------------------------------
            # KEYFRAME REQUEST SENT
            # --------------------------------------------------------
            match = KEYFRAME_REQUEST_SENT_RE.search(
                line
            )

            if match:
                keyframe_request_sent_events.append(
                    {
                        "line_no": line_no,
                        "pairs": extract_pairs(
                            match.group("body")
                        ),
                    }
                )

                continue

            # --------------------------------------------------------
            # KEYFRAME REQUEST
            # --------------------------------------------------------
            match = KEYFRAME_REQUEST_RE.search(line)

            if match:
                keyframe_request_events.append(
                    {
                        "line_no": line_no,
                        "pairs": extract_pairs(
                            match.group("body")
                        ),
                    }
                )

                continue

            # --------------------------------------------------------
            # ALREADY DECODED CHECK
            # --------------------------------------------------------
            match = ALREADY_DECODED_CHECK_RE.search(
                line
            )

            if match:
                pairs = extract_pairs(
                    match.group("body")
                )

                frame_id = pairs.get("frame_id")

                if frame_id is not None:
                    pending_checks[frame_id] = pairs
                    pending_references[frame_id] = []

                continue

            # --------------------------------------------------------
            # ALREADY DECODED REFERENCE
            # --------------------------------------------------------
            match = (
                ALREADY_DECODED_REFERENCE_RE.search(
                    line
                )
            )

            if match:
                pairs = extract_pairs(
                    match.group("body")
                )

                frame_id = pairs.get("frame_id")
                reference_frame_id = pairs.get(
                    "reference_frame_id"
                )

                if (
                    frame_id is None
                    or reference_frame_id is None
                ):
                    continue

                reference_index = parse_int(
                    pairs.get("reference_index")
                )

                if reference_index is None:
                    reference_index = 0

                pending_references.setdefault(
                    frame_id,
                    [],
                ).append(
                    (
                        reference_index,
                        reference_frame_id,
                    )
                )

                continue

            # --------------------------------------------------------
            # FRAME DROP
            # --------------------------------------------------------
            match = FRAME_DROP_RE.search(line)

            if not match:
                continue

            drop_pairs: dict[str, Any] = (
                extract_pairs(
                    match.group("body")
                )
            )

            frame_id = drop_pairs.get("frame_id")

            # FRAME ALREADY DECODED CHECK와 병합
            if (
                frame_id is not None
                and frame_id in pending_checks
            ):
                check_pairs = pending_checks.pop(
                    frame_id
                )

                for key, value in check_pairs.items():
                    drop_pairs.setdefault(key, value)

            # reference ID 목록 병합
            if (
                frame_id is not None
                and frame_id in pending_references
            ):
                references = pending_references.pop(
                    frame_id
                )

                references.sort(
                    key=lambda item: item[0]
                )

                drop_pairs["reference_frame_ids"] = (
                    ",".join(
                        reference_frame_id
                        for _, reference_frame_id
                        in references
                    )
                )

            row: dict[str, Any] = {
                field: ""
                for field in FIELDNAMES
            }

            row["drop_line_no"] = line_no

            copy_drop_fields(
                row,
                drop_pairs,
            )

            # 드랍된 프레임의 COMPLETE 정보
            dropped_complete_event = (
                latest_frame_event(
                    complete_events,
                    frame_id,
                )
            )

            copy_complete_event(
                row,
                dropped_complete_event,
                "dropped_complete",
            )

            # last decoded frame의 COMPLETE / EXTRACT 정보
            last_decoded_frame_id = first_value(
                drop_pairs,
                "last_decoded_frame_id",
            )

            last_frame_id_key = (
                str(last_decoded_frame_id)
                if last_decoded_frame_id != ""
                else None
            )

            last_complete_event = (
                latest_frame_event(
                    complete_events,
                    last_frame_id_key,
                )
            )

            last_extract_event = (
                latest_frame_event(
                    extract_events,
                    last_frame_id_key,
                )
            )

            copy_complete_event(
                row,
                last_complete_event,
                "last_complete",
            )

            copy_extract_event(
                row,
                last_extract_event,
            )

            add_keyframe_request_fields(
                row,
                line_no,
                keyframe_request_events,
                keyframe_request_sent_events,
            )

            add_derived_analysis(row)

            validate_row(
                row,
                len(rows),
            )

            rows.append(row)

    output_path = Path(OUTPUT_CSV)

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output_path.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as output_file:
        writer = csv.DictWriter(
            output_file,
            fieldnames=FIELDNAMES,
        )

        writer.writeheader()
        writer.writerows(rows)

    reason_counts: dict[str, int] = {}
    cause_counts: dict[str, int] = {}

    for row in rows:
        reason = str(
            row.get("reason", "")
        )

        cause = str(
            row.get("likely_cause", "")
        )

        reason_counts[reason] = (
            reason_counts.get(reason, 0) + 1
        )

        cause_counts[cause] = (
            cause_counts.get(cause, 0) + 1
        )

    print(f"[INFO] input : {INPUT_LOG}")
    print(f"[INFO] output: {OUTPUT_CSV}")
    print(
        f"[INFO] parsed frame drops: {len(rows)}"
    )

    print("[INFO] drop reasons:")

    for reason, count in sorted(
        reason_counts.items()
    ):
        print(
            f"[INFO]   {reason}: {count}"
        )

    print("[INFO] likely causes:")

    for cause, count in sorted(
        cause_counts.items()
    ):
        print(
            f"[INFO]   {cause}: {count}"
        )


if __name__ == "__main__":
    main()