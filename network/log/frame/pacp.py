#!/usr/bin/env python3

import argparse
import os
import sys
from decimal import Decimal, InvalidOperation, getcontext

import pandas as pd


# frame.time_epoch의 소수부 정밀도를 잃지 않도록 충분히 크게 설정한다.
getcontext().prec = 50


DEFAULT_FRAME_PACKETS_CSV = "csv/frame_packets.csv"
DEFAULT_LATE_DROP_CSV = "csv/late_drop_cause_analysis.csv"
DEFAULT_PCAP_CSV = "tshark/rtp_packets.csv"

# 전체 normal/late/drop 문맥
DEFAULT_ALL_PACKET_OUTPUT_CSV = "csv/all_packet_send_receive.csv"
DEFAULT_ALL_FRAME_OUTPUT_CSV = "csv/all_frame_pcap_receiver_timing.csv"

# late/drop 대상 상세 결과
DEFAULT_TARGET_PACKET_OUTPUT_CSV = "csv/late_drop_packet_send_receive.csv"
DEFAULT_TARGET_PACKET_OUTPUT_TXT = "tshark/late_drop_packet_send_receive.txt"
DEFAULT_TARGET_FRAME_OUTPUT_CSV = "csv/pcap_receiver_packet_compare.csv"
DEFAULT_TARGET_FRAME_OUTPUT_TXT = "tshark/pcap_packet_span_simple.txt"


RTP_TIMESTAMP_MODULO = 1 << 32
RTP_TIMESTAMP_HALF = 1 << 31

# 일반적인 WebRTC 비디오 RTP clock.
DEFAULT_VIDEO_RTP_CLOCK_HZ = 90000


def ensure_parent(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def read_csv_as_text(path: str) -> pd.DataFrame:
    """
    CSV를 문자열로 읽는다.

    frame.time_epoch를 float로 읽으면 큰 epoch 값에서 소수부 정밀도를
    잃을 수 있으므로 모든 열을 문자열로 보존한다.
    """
    df = pd.read_csv(
        path,
        dtype=str,
        keep_default_na=False,
    )

    df.columns = df.columns.str.strip()

    for col in df.columns:
        df[col] = df[col].astype(str).str.strip()

    return df


def require_columns(
    df: pd.DataFrame,
    required: list[str],
    file_name: str,
) -> None:
    missing = [col for col in required if col not in df.columns]

    if missing:
        raise ValueError(
            f"{file_name} missing required columns: {missing}"
        )


def parse_int(value, field_name: str) -> int:
    text = str(value).strip()

    try:
        return int(text, 0)
    except (TypeError, ValueError):
        try:
            # CSV에 123.0처럼 저장된 정수도 허용한다.
            decimal_value = Decimal(text)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(
                f"Invalid integer value for {field_name}: {value!r}"
            ) from exc

        if decimal_value != decimal_value.to_integral_value():
            raise ValueError(
                f"Non-integer value for {field_name}: {value!r}"
            )

        return int(decimal_value)


def parse_decimal(value, field_name: str) -> Decimal:
    text = str(value).strip()

    try:
        return Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(
            f"Invalid decimal value for {field_name}: {value!r}"
        ) from exc


def decimal_or_none(value) -> Decimal | None:
    if value is None:
        return None

    text = str(value).strip()

    if text == "" or text.lower() in {"nan", "none", "<na>"}:
        return None

    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def decimal_to_text(value: Decimal | None) -> str:
    """
    과학적 표기법과 임의 반올림 없이 고정 소수점 문자열로 출력한다.
    """
    if value is None:
        return ""

    return format(value, "f")


def bool_to_text(value) -> str:
    return "True" if bool(value) else "False"


def load_late_drop_frames(path: str) -> pd.DataFrame:
    """
    late/drop 분석 대상 frameId와 부가 정보를 읽는다.

    이 파일은 대상 목록으로만 사용한다.
    normal 프레임은 frame_packets.csv 전체에서 유지된다.
    """
    df = read_csv_as_text(path)

    require_columns(
        df,
        ["frameId"],
        path,
    )

    optional_columns = [
        "frameClass",
        "primaryCause",
        "max_wait",
        "preDecodeWaitingMs",
    ]

    for col in optional_columns:
        if col not in df.columns:
            df[col] = ""

    df = df[df["frameId"] != ""].copy()

    df["frameId"] = df["frameId"].apply(
        lambda value: parse_int(value, "frameId")
    )

    duplicate_mask = df.duplicated(
        subset=["frameId"],
        keep=False,
    )

    if duplicate_mask.any():
        duplicated = df.loc[
            duplicate_mask,
            [
                "frameId",
                "frameClass",
                "primaryCause",
                "max_wait",
            ],
        ]

        print(
            "[WARN] late/drop CSV contains duplicate frameId rows. "
            "The first row for each frameId will be used.",
            file=sys.stderr,
        )
        print(
            duplicated.head(20).to_string(index=False),
            file=sys.stderr,
        )

    df = df.drop_duplicates(
        subset=["frameId"],
        keep="first",
    )

    return df[
        [
            "frameId",
            "frameClass",
            "primaryCause",
            "max_wait",
            "preDecodeWaitingMs",
        ]
    ]


def load_receiver_packets(path: str) -> pd.DataFrame:
    """
    frame_packets.csv 전체를 읽는다.

    이 파일이 normal/late/drop을 포함한 전체 프레임-패킷 소속 관계의
    기준이다.
    """
    df = read_csv_as_text(path)

    require_columns(
        df,
        [
            "frameId",
            "sequenceNumber",
            "receiveTimeMs",
        ],
        path,
    )

    df = df[
        (df["frameId"] != "")
        & (df["sequenceNumber"] != "")
        & (df["receiveTimeMs"] != "")
    ].copy()

    df["frameId"] = df["frameId"].apply(
        lambda value: parse_int(value, "frameId")
    )

    df["sequenceNumber"] = df["sequenceNumber"].apply(
        lambda value: parse_int(value, "sequenceNumber")
    )

    # 원본 문자열을 보존하되 값이 유효한 Decimal인지 확인한다.
    df["receiveTimeMs"].apply(
        lambda value: parse_decimal(value, "receiveTimeMs")
    )

    # frame_packets.csv에서의 실제 기록 순서.
    df["receiver_csv_order"] = range(len(df))

    duplicate_mask = df.duplicated(
        subset=[
            "frameId",
            "sequenceNumber",
        ],
        keep=False,
    )

    if duplicate_mask.any():
        duplicated = df.loc[
            duplicate_mask,
            [
                "frameId",
                "sequenceNumber",
                "receiveTimeMs",
            ],
        ]

        raise ValueError(
            "frame_packets.csv contains duplicate "
            "frameId + sequenceNumber keys.\n"
            "A packet must be uniquely identified before exact matching.\n"
            f"{duplicated.head(30).to_string(index=False)}"
        )

    return df


def normalize_ssrc_text(value: str) -> int:
    return parse_int(value, "rtp.ssrc")


def load_pcap(
    path: str,
    media_ssrc: int | None,
) -> pd.DataFrame:
    """
    tshark RTP CSV를 읽고 선택적으로 media SSRC를 제한한다.
    """
    df = read_csv_as_text(path)

    require_columns(
        df,
        [
            "rtp.timestamp",
            "rtp.seq",
            "frame.time_epoch",
        ],
        path,
    )

    df = df[
        (df["rtp.timestamp"] != "")
        & (df["rtp.seq"] != "")
        & (df["frame.time_epoch"] != "")
    ].copy()

    df["rtp.timestamp"] = df["rtp.timestamp"].apply(
        lambda value: parse_int(value, "rtp.timestamp")
    )

    df["rtp.seq"] = df["rtp.seq"].apply(
        lambda value: parse_int(value, "rtp.seq")
    )

    df["frame.time_epoch"].apply(
        lambda value: parse_decimal(value, "frame.time_epoch")
    )

    if media_ssrc is not None:
        if "rtp.ssrc" not in df.columns:
            raise ValueError(
                "--media-ssrc was provided, but rtp.ssrc does not "
                "exist in the PCAP CSV."
            )

        valid_ssrc_mask = df["rtp.ssrc"] != ""

        df = df[
            valid_ssrc_mask
            & (
                df["rtp.ssrc"].apply(normalize_ssrc_text)
                == media_ssrc
            )
        ].copy()

        print(f"[INFO] PCAP media SSRC filter: {media_ssrc}")

    # 같은 timestamp + seq가 여러 행이면 정확히 어느 패킷인지 모호하다.
    duplicate_mask = df.duplicated(
        subset=[
            "rtp.timestamp",
            "rtp.seq",
        ],
        keep=False,
    )

    if duplicate_mask.any():
        columns = [
            "rtp.timestamp",
            "rtp.seq",
            "frame.time_epoch",
        ]

        if "rtp.ssrc" in df.columns:
            columns.append("rtp.ssrc")

        duplicated = df.loc[
            duplicate_mask,
            columns,
        ]

        raise ValueError(
            "PCAP contains multiple rows with the same "
            "rtp.timestamp + rtp.seq.\n"
            "Exact packet identification is ambiguous. "
            "Specify the media SSRC with --media-ssrc or filter the "
            "PCAP flow first.\n"
            f"{duplicated.head(30).to_string(index=False)}"
        )

    return df


def attach_target_metadata(
    receiver_packets: pd.DataFrame,
    late_drop_frames: pd.DataFrame,
) -> pd.DataFrame:
    """
    frame_packets.csv 전체를 기준으로 late/drop 메타데이터를 left join한다.

    normal 프레임은 제거하지 않는다.
    """
    merged = receiver_packets.merge(
        late_drop_frames,
        on="frameId",
        how="left",
        validate="many_to_one",
        indicator=True,
    )

    merged["is_target_frame"] = merged["_merge"] == "both"
    merged = merged.drop(columns=["_merge"])

    merged["frameClass"] = merged["frameClass"].fillna("")
    merged["primaryCause"] = merged["primaryCause"].fillna("")
    merged["max_wait"] = merged["max_wait"].fillna("")
    merged["preDecodeWaitingMs"] = (
        merged["preDecodeWaitingMs"].fillna("")
    )

    merged.loc[
        ~merged["is_target_frame"],
        "frameClass",
    ] = "NORMAL_CONTEXT"

    return merged


def build_all_packet_result(
    all_packets: pd.DataFrame,
    pcap: pd.DataFrame,
) -> pd.DataFrame:
    """
    frame_packets.csv의 모든 패킷을 PCAP과 정확히 직접 매칭한다.

    매칭 키:
        frameId == rtp.timestamp
        sequenceNumber == rtp.seq
    """
    pcap_columns = [
        "rtp.timestamp",
        "rtp.seq",
        "frame.time_epoch",
    ]

    optional_columns = [
        "rtp.ssrc",
        "frame.number",
        "ip.src",
        "ip.dst",
        "udp.srcport",
        "udp.dstport",
    ]

    for col in optional_columns:
        if col in pcap.columns:
            pcap_columns.append(col)

    packet_df = all_packets.merge(
        pcap[pcap_columns],
        left_on=[
            "frameId",
            "sequenceNumber",
        ],
        right_on=[
            "rtp.timestamp",
            "rtp.seq",
        ],
        how="left",
        validate="one_to_one",
    )

    packet_df["pcap_matched"] = (
        packet_df["frame.time_epoch"]
        .fillna("")
        .astype(str)
        .str.strip()
        != ""
    )

    packet_df["pcap_send_epoch_s"] = (
        packet_df["frame.time_epoch"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    packet_df["pcap_send_epoch_ms"] = packet_df[
        "pcap_send_epoch_s"
    ].apply(
        lambda value: (
            decimal_to_text(
                parse_decimal(value, "frame.time_epoch")
                * Decimal("1000")
            )
            if value != ""
            else ""
        )
    )

    packet_df = packet_df.sort_values(
        [
            "frameId",
            "sequenceNumber",
            "receiver_csv_order",
        ]
    ).reset_index(drop=True)

    packet_df["packet_index_in_frame"] = (
        packet_df.groupby("frameId").cumcount() + 1
    )

    packet_df[
        "pcap_send_gap_from_prev_packet_ms"
    ] = ""
    packet_df[
        "receive_gap_from_prev_packet_ms"
    ] = ""
    packet_df[
        "receive_gap_minus_send_gap_ms"
    ] = ""

    # 프레임 내부에서 이전 sequence packet과의 실제 간격을 계산한다.
    for _, frame_df in packet_df.groupby(
        "frameId",
        sort=False,
    ):
        previous_send = None
        previous_receive = None

        for index in frame_df.index:
            current_send = decimal_or_none(
                packet_df.at[index, "pcap_send_epoch_ms"]
            )
            current_receive = decimal_or_none(
                packet_df.at[index, "receiveTimeMs"]
            )

            if (
                current_send is not None
                and previous_send is not None
            ):
                send_gap = current_send - previous_send
                packet_df.at[
                    index,
                    "pcap_send_gap_from_prev_packet_ms",
                ] = decimal_to_text(send_gap)
            else:
                send_gap = None

            if (
                current_receive is not None
                and previous_receive is not None
            ):
                receive_gap = current_receive - previous_receive
                packet_df.at[
                    index,
                    "receive_gap_from_prev_packet_ms",
                ] = decimal_to_text(receive_gap)
            else:
                receive_gap = None

            if send_gap is not None and receive_gap is not None:
                packet_df.at[
                    index,
                    "receive_gap_minus_send_gap_ms",
                ] = decimal_to_text(receive_gap - send_gap)

            previous_send = current_send
            previous_receive = current_receive

    return packet_df


def assign_unwrapped_rtp_timestamps(
    frame_summary: pd.DataFrame,
) -> pd.DataFrame:
    """
    first receiver appearance 순서를 이용해 32-bit RTP timestamp를 unwrap한다.

    작은 out-of-order는 그대로 두고, 2^31보다 큰 점프만 wrap으로 본다.
    그 후 unwrapped RTP timestamp 순으로 프레임을 정렬한다.
    """
    df = frame_summary.sort_values(
        "first_receiver_csv_order"
    ).copy()

    wrap_offset = 0
    previous_raw = None
    unwrapped_values: list[int] = []

    for raw_value in df["frameId"].astype("int64"):
        raw = int(raw_value)

        if previous_raw is not None:
            delta = raw - previous_raw

            if delta < -RTP_TIMESTAMP_HALF:
                wrap_offset += RTP_TIMESTAMP_MODULO
            elif delta > RTP_TIMESTAMP_HALF:
                wrap_offset -= RTP_TIMESTAMP_MODULO

        unwrapped_values.append(raw + wrap_offset)
        previous_raw = raw

    df["rtp_timestamp_unwrapped"] = unwrapped_values

    df = df.sort_values(
        [
            "rtp_timestamp_unwrapped",
            "first_receiver_csv_order",
        ]
    ).reset_index(drop=True)

    return df


def build_all_frame_summary(
    packet_df: pd.DataFrame,
    rtp_clock_hz: int,
) -> pd.DataFrame:
    """
    normal/late/drop을 포함한 모든 프레임의 송수신 정보를 요약한다.
    """
    rows: list[dict] = []

    for frame_id, frame_df in packet_df.groupby(
        "frameId",
        sort=False,
    ):
        frame_df = frame_df.sort_values(
            [
                "sequenceNumber",
                "receiver_csv_order",
            ]
        )

        first_row = frame_df.iloc[0]

        send_times = [
            decimal_or_none(value)
            for value in frame_df["pcap_send_epoch_ms"]
        ]
        send_times = [
            value for value in send_times if value is not None
        ]

        receive_times = [
            decimal_or_none(value)
            for value in frame_df["receiveTimeMs"]
        ]
        receive_times = [
            value for value in receive_times if value is not None
        ]

        pcap_first_send = min(send_times) if send_times else None
        pcap_last_send = max(send_times) if send_times else None
        receiver_first = min(receive_times) if receive_times else None
        receiver_last = max(receive_times) if receive_times else None

        pcap_span = (
            pcap_last_send - pcap_first_send
            if (
                pcap_first_send is not None
                and pcap_last_send is not None
            )
            else None
        )

        receiver_span = (
            receiver_last - receiver_first
            if (
                receiver_first is not None
                and receiver_last is not None
            )
            else None
        )

        matched_count = int(frame_df["pcap_matched"].sum())
        receiver_count = len(frame_df)

        rows.append(
            {
                "frameId": int(frame_id),
                "first_receiver_csv_order": int(
                    frame_df["receiver_csv_order"].min()
                ),
                "is_target_frame": bool(
                    first_row["is_target_frame"]
                ),
                "frameClass": first_row["frameClass"],
                "primaryCause": first_row["primaryCause"],
                "max_wait": first_row["max_wait"],
                "preDecodeWaitingMs": first_row[
                    "preDecodeWaitingMs"
                ],
                "receiver_packet_count": receiver_count,
                "pcap_packet_count": matched_count,
                "first_seq": int(
                    frame_df["sequenceNumber"].min()
                ),
                "last_seq": int(
                    frame_df["sequenceNumber"].max()
                ),
                "pcap_first_send_epoch_ms": decimal_to_text(
                    pcap_first_send
                ),
                "pcap_last_send_epoch_ms": decimal_to_text(
                    pcap_last_send
                ),
                "recv_first_ms": decimal_to_text(receiver_first),
                "recv_last_ms": decimal_to_text(receiver_last),
                "pcap_packet_span_ms": decimal_to_text(
                    pcap_span
                ),
                "receiver_packet_span_ms": decimal_to_text(
                    receiver_span
                ),
                "pcap_matched": matched_count > 0,
                "pcap_all_matched": (
                    receiver_count > 0
                    and matched_count == receiver_count
                ),
                "match_mode": "direct",
                "timestamp_offset": 0,
            }
        )

    summary = pd.DataFrame(rows)

    if summary.empty:
        return summary

    summary = assign_unwrapped_rtp_timestamps(summary)

    # 이전 프레임 문맥 열
    context_columns = [
        "previous_frame_id",
        "previous_frame_class",
        "previous_frame_is_target",
        "previous_pcap_all_matched",
        "previous_pcap_first_send_epoch_ms",
        "previous_pcap_last_send_epoch_ms",
        "previous_recv_first_ms",
        "rtp_timestamp_gap_ticks",
        "expected_frame_gap_ms",
        "pcap_first_send_gap_from_prev_frame_ms",
        "receiver_first_receive_gap_from_prev_frame_ms",
        "sender_interframe_gap_excess_ms",
        "receiver_interframe_gap_excess_ms",
        "network_added_interframe_gap_ms",
        "pcap_gap_from_previous_frame_last_packet_ms",
        "baseline_frame_id",
        "sender_schedule_delay_from_baseline_ms",
        "receiver_schedule_delay_from_baseline_ms",
        "network_schedule_delay_from_baseline_ms",
    ]

    for col in context_columns:
        summary[col] = ""

    previous_row = None

    # 누적 스케줄 지연의 기준은 양쪽 시각이 모두 있는 첫 프레임.
    baseline_frame_id = None
    baseline_rtp_unwrapped = None
    baseline_send = None
    baseline_receive = None

    rtp_clock = Decimal(rtp_clock_hz)

    for index, row in summary.iterrows():
        current_rtp_unwrapped = int(
            row["rtp_timestamp_unwrapped"]
        )

        current_send = decimal_or_none(
            row["pcap_first_send_epoch_ms"]
        )
        current_receive = decimal_or_none(
            row["recv_first_ms"]
        )

        if (
            baseline_frame_id is None
            and current_send is not None
            and current_receive is not None
        ):
            baseline_frame_id = int(row["frameId"])
            baseline_rtp_unwrapped = current_rtp_unwrapped
            baseline_send = current_send
            baseline_receive = current_receive

        if baseline_frame_id is not None:
            summary.at[
                index,
                "baseline_frame_id",
            ] = str(baseline_frame_id)

            expected_from_baseline_ms = (
                Decimal(
                    current_rtp_unwrapped
                    - int(baseline_rtp_unwrapped)
                )
                * Decimal("1000")
                / rtp_clock
            )

            if current_send is not None and baseline_send is not None:
                actual_send_from_baseline = (
                    current_send - baseline_send
                )
                sender_schedule_delay = (
                    actual_send_from_baseline
                    - expected_from_baseline_ms
                )

                summary.at[
                    index,
                    "sender_schedule_delay_from_baseline_ms",
                ] = decimal_to_text(sender_schedule_delay)

            if (
                current_receive is not None
                and baseline_receive is not None
            ):
                actual_receive_from_baseline = (
                    current_receive - baseline_receive
                )
                receiver_schedule_delay = (
                    actual_receive_from_baseline
                    - expected_from_baseline_ms
                )

                summary.at[
                    index,
                    "receiver_schedule_delay_from_baseline_ms",
                ] = decimal_to_text(receiver_schedule_delay)

            sender_schedule_delay_value = decimal_or_none(
                summary.at[
                    index,
                    "sender_schedule_delay_from_baseline_ms",
                ]
            )
            receiver_schedule_delay_value = decimal_or_none(
                summary.at[
                    index,
                    "receiver_schedule_delay_from_baseline_ms",
                ]
            )

            if (
                sender_schedule_delay_value is not None
                and receiver_schedule_delay_value is not None
            ):
                summary.at[
                    index,
                    "network_schedule_delay_from_baseline_ms",
                ] = decimal_to_text(
                    receiver_schedule_delay_value
                    - sender_schedule_delay_value
                )

        if previous_row is None:
            previous_row = row.copy()
            continue

        previous_frame_id = int(previous_row["frameId"])
        previous_rtp_unwrapped = int(
            previous_row["rtp_timestamp_unwrapped"]
        )

        previous_send = decimal_or_none(
            previous_row["pcap_first_send_epoch_ms"]
        )
        previous_last_send = decimal_or_none(
            previous_row["pcap_last_send_epoch_ms"]
        )
        previous_receive = decimal_or_none(
            previous_row["recv_first_ms"]
        )

        rtp_gap_ticks = (
            current_rtp_unwrapped - previous_rtp_unwrapped
        )

        expected_gap_ms = (
            Decimal(rtp_gap_ticks)
            * Decimal("1000")
            / rtp_clock
        )

        summary.at[
            index,
            "previous_frame_id",
        ] = str(previous_frame_id)

        summary.at[
            index,
            "previous_frame_class",
        ] = str(previous_row["frameClass"])

        summary.at[
            index,
            "previous_frame_is_target",
        ] = bool_to_text(
            previous_row["is_target_frame"]
        )

        summary.at[
            index,
            "previous_pcap_all_matched",
        ] = bool_to_text(
            previous_row["pcap_all_matched"]
        )

        summary.at[
            index,
            "previous_pcap_first_send_epoch_ms",
        ] = str(
            previous_row["pcap_first_send_epoch_ms"]
        )

        summary.at[
            index,
            "previous_pcap_last_send_epoch_ms",
        ] = str(
            previous_row["pcap_last_send_epoch_ms"]
        )

        summary.at[
            index,
            "previous_recv_first_ms",
        ] = str(previous_row["recv_first_ms"])

        summary.at[
            index,
            "rtp_timestamp_gap_ticks",
        ] = str(rtp_gap_ticks)

        summary.at[
            index,
            "expected_frame_gap_ms",
        ] = decimal_to_text(expected_gap_ms)

        send_gap = None
        receive_gap = None

        if current_send is not None and previous_send is not None:
            send_gap = current_send - previous_send

            summary.at[
                index,
                "pcap_first_send_gap_from_prev_frame_ms",
            ] = decimal_to_text(send_gap)

            summary.at[
                index,
                "sender_interframe_gap_excess_ms",
            ] = decimal_to_text(
                send_gap - expected_gap_ms
            )

        if (
            current_receive is not None
            and previous_receive is not None
        ):
            receive_gap = current_receive - previous_receive

            summary.at[
                index,
                "receiver_first_receive_gap_from_prev_frame_ms",
            ] = decimal_to_text(receive_gap)

            summary.at[
                index,
                "receiver_interframe_gap_excess_ms",
            ] = decimal_to_text(
                receive_gap - expected_gap_ms
            )

        if send_gap is not None and receive_gap is not None:
            summary.at[
                index,
                "network_added_interframe_gap_ms",
            ] = decimal_to_text(receive_gap - send_gap)

        if (
            current_send is not None
            and previous_last_send is not None
        ):
            summary.at[
                index,
                "pcap_gap_from_previous_frame_last_packet_ms",
            ] = decimal_to_text(
                current_send - previous_last_send
            )

        previous_row = row.copy()

    return summary


def write_target_packet_txt(
    packet_df: pd.DataFrame,
    output_path: str,
) -> None:
    ensure_parent(output_path)

    columns = [
        "packet_index_in_frame",
        "sequenceNumber",
        "pcap_send_epoch_s",
        "pcap_send_epoch_ms",
        "receiveTimeMs",
        "pcap_send_gap_from_prev_packet_ms",
        "receive_gap_from_prev_packet_ms",
        "receive_gap_minus_send_gap_ms",
        "pcap_matched",
    ]

    with open(
        output_path,
        "w",
        encoding="utf-8",
    ) as output:
        for frame_id, frame_df in packet_df.groupby(
            "frameId",
            sort=True,
        ):
            frame_df = frame_df.sort_values(
                [
                    "sequenceNumber",
                    "receiver_csv_order",
                ]
            )

            first = frame_df.iloc[0]

            output.write("=" * 220 + "\n")
            output.write(
                f"frameId={frame_id} "
                f"frameClass={first['frameClass']} "
                f"primaryCause={first['primaryCause']} "
                f"max_wait={first['max_wait']} "
                f"packet_count={len(frame_df)} "
                f"matched={int(frame_df['pcap_matched'].sum())}"
                f"/{len(frame_df)}\n\n"
            )

            output.write(
                frame_df[columns].to_string(
                    index=False,
                    justify="right",
                )
            )
            output.write("\n\n")


def write_target_frame_txt(
    frame_summary: pd.DataFrame,
    output_path: str,
) -> None:
    ensure_parent(output_path)

    columns = [
        "frameId",
        "frameClass",
        "pcap_packet_count",
        "receiver_packet_count",
        "pcap_first_send_epoch_ms",
        "pcap_last_send_epoch_ms",
        "recv_first_ms",
        "recv_last_ms",
        "pcap_packet_span_ms",
        "receiver_packet_span_ms",
        "previous_frame_id",
        "previous_frame_class",
        "expected_frame_gap_ms",
        "pcap_first_send_gap_from_prev_frame_ms",
        "receiver_first_receive_gap_from_prev_frame_ms",
        "sender_interframe_gap_excess_ms",
        "receiver_interframe_gap_excess_ms",
        "network_added_interframe_gap_ms",
        "sender_schedule_delay_from_baseline_ms",
        "receiver_schedule_delay_from_baseline_ms",
        "network_schedule_delay_from_baseline_ms",
        "pcap_all_matched",
    ]

    with open(
        output_path,
        "w",
        encoding="utf-8",
    ) as output:
        output.write(
            frame_summary[columns].to_string(
                index=False,
                justify="right",
            )
        )
        output.write("\n")


def save_csv_without_float_conversion(
    df: pd.DataFrame,
    path: str,
) -> None:
    ensure_parent(path)
    df.to_csv(path, index=False)


def print_run_summary(
    all_packet_df: pd.DataFrame,
    all_frame_summary: pd.DataFrame,
    target_packet_df: pd.DataFrame,
    target_frame_summary: pd.DataFrame,
    args,
) -> None:
    all_packet_count = len(all_packet_df)
    all_matched_packet_count = int(
        all_packet_df["pcap_matched"].sum()
    )

    target_packet_count = len(target_packet_df)
    target_matched_packet_count = int(
        target_packet_df["pcap_matched"].sum()
    )

    target_fully_matched_frames = int(
        target_frame_summary["pcap_all_matched"].sum()
    )

    print("")
    print(f"[INFO] all frames                 : {len(all_frame_summary)}")
    print(f"[INFO] all packets                : {all_packet_count}")
    print(
        f"[INFO] all matched packets        : "
        f"{all_matched_packet_count}/{all_packet_count}"
    )
    print(f"[INFO] target late/drop frames    : {len(target_frame_summary)}")
    print(f"[INFO] target late/drop packets   : {target_packet_count}")
    print(
        f"[INFO] target matched packets     : "
        f"{target_matched_packet_count}/{target_packet_count}"
    )
    print(
        f"[INFO] target fully matched frames: "
        f"{target_fully_matched_frames}/{len(target_frame_summary)}"
    )
    print("")
    print(f"[INFO] all packet CSV   : {args.all_packet_output}")
    print(f"[INFO] all frame CSV    : {args.all_frame_output}")
    print(f"[INFO] target packet CSV: {args.target_packet_output}")
    print(f"[INFO] target packet TXT: {args.target_packet_text_output}")
    print(f"[INFO] target frame CSV : {args.target_frame_output}")
    print(f"[INFO] target frame TXT : {args.target_frame_text_output}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Match every frame_packets.csv packet with PCAP, preserve "
            "absolute timestamps exactly, build normal-frame context, "
            "and finally output late/drop packet and frame analysis."
        )
    )

    parser.add_argument(
        "--frame-packets",
        default=DEFAULT_FRAME_PACKETS_CSV,
    )

    parser.add_argument(
        "--late-drop",
        default=DEFAULT_LATE_DROP_CSV,
    )

    parser.add_argument(
        "--pcap",
        default=DEFAULT_PCAP_CSV,
    )

    parser.add_argument(
        "--all-packet-output",
        default=DEFAULT_ALL_PACKET_OUTPUT_CSV,
    )

    parser.add_argument(
        "--all-frame-output",
        default=DEFAULT_ALL_FRAME_OUTPUT_CSV,
    )

    parser.add_argument(
        "--target-packet-output",
        default=DEFAULT_TARGET_PACKET_OUTPUT_CSV,
    )

    parser.add_argument(
        "--target-packet-text-output",
        default=DEFAULT_TARGET_PACKET_OUTPUT_TXT,
    )

    parser.add_argument(
        "--target-frame-output",
        default=DEFAULT_TARGET_FRAME_OUTPUT_CSV,
    )

    parser.add_argument(
        "--target-frame-text-output",
        default=DEFAULT_TARGET_FRAME_OUTPUT_TXT,
    )

    parser.add_argument(
        "--media-ssrc",
        type=int,
        default=None,
        help=(
            "Exact media RTP SSRC. Use this when the PCAP contains "
            "multiple RTP streams."
        ),
    )

    parser.add_argument(
        "--rtp-clock-hz",
        type=int,
        default=DEFAULT_VIDEO_RTP_CLOCK_HZ,
        help=(
            "RTP clock rate. WebRTC video normally uses 90000 Hz."
        ),
    )

    args = parser.parse_args()

    if args.rtp_clock_hz <= 0:
        raise ValueError("--rtp-clock-hz must be positive.")

    receiver_packets = load_receiver_packets(
        args.frame_packets
    )

    late_drop_frames = load_late_drop_frames(
        args.late_drop
    )

    pcap = load_pcap(
        args.pcap,
        args.media_ssrc,
    )

    # 핵심: frame_packets.csv 전체를 유지한 상태에서 대상 메타데이터만 붙인다.
    all_packets = attach_target_metadata(
        receiver_packets,
        late_drop_frames,
    )

    # normal/late/drop 전체 패킷을 PCAP과 먼저 매칭한다.
    all_packet_df = build_all_packet_result(
        all_packets,
        pcap,
    )

    # 전체 프레임 문맥에서 실제 이전 normal/late/drop 프레임을 계산한다.
    all_frame_summary = build_all_frame_summary(
        all_packet_df,
        rtp_clock_hz=args.rtp_clock_hz,
    )

    target_frame_summary = all_frame_summary[
        all_frame_summary["is_target_frame"]
    ].copy()

    target_frame_ids = set(
        target_frame_summary["frameId"].astype("int64")
    )

    target_packet_df = all_packet_df[
        all_packet_df["frameId"].isin(target_frame_ids)
    ].copy()

    # 전체 문맥 파일
    save_csv_without_float_conversion(
        all_packet_df,
        args.all_packet_output,
    )

    save_csv_without_float_conversion(
        all_frame_summary,
        args.all_frame_output,
    )

    # late/drop 대상 파일
    save_csv_without_float_conversion(
        target_packet_df,
        args.target_packet_output,
    )

    save_csv_without_float_conversion(
        target_frame_summary,
        args.target_frame_output,
    )

    write_target_packet_txt(
        target_packet_df,
        args.target_packet_text_output,
    )

    write_target_frame_txt(
        target_frame_summary,
        args.target_frame_text_output,
    )

    print_run_summary(
        all_packet_df,
        all_frame_summary,
        target_packet_df,
        target_frame_summary,
        args,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
