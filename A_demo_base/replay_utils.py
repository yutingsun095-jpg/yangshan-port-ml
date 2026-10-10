"""Time-window and event-chain presentation helpers; no event reclassification."""

from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from data_loader import utc_connection


EVENT_ORDER = {"ANC": 0, "ARR": 1, "BER": 2, "UNB": 3, "DEP": 4}


def utc(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError("Formal event timestamp must be non-null and UTC-aware")
    return stamp.tz_convert("UTC")


def event_of_type(events: pd.DataFrame, kind: str) -> Optional[pd.Series]:
    rows = events.loc[events.event_type.eq(kind)].sort_values("event_time_utc")
    return None if rows.empty else rows.iloc[0]


def visit_query_window(events: pd.DataFrame) -> Tuple[pd.Timestamp, pd.Timestamp, str, str]:
    """Bound the selected visit using only its already-linked formal events."""
    arr = event_of_type(events, "ARR")
    if arr is None:
        raise ValueError("Selected Port Call has no formal ARR")
    anc = events.loc[events.event_type.eq("ANC")].sort_values("event_time_utc")
    if not anc.empty:
        start = utc(anc.iloc[0].event_time_utc) - timedelta(minutes=30)
        start_rule = "首个正式关联ANC开始 − 30 min"
    else:
        start = utc(arr.event_time_utc) - timedelta(hours=6)
        start_rule = "ARR − 6 h"
    for kind, minutes, rule in [
        ("DEP", 30, "DEP + 30 min"),
        ("UNB", 90, "UNB + 90 min"),
        ("BER", 360, "BER + 6 h"),
        ("ARR", 180, "ARR + 3 h"),
    ]:
        event = event_of_type(events, kind)
        if event is not None:
            end = utc(event.event_time_utc) + timedelta(minutes=minutes)
            if end <= start:
                raise ValueError("Formal-event query window is non-positive")
            return start, end, start_rule, rule
    raise AssertionError("ARR existence checked above")


def event_chain(events: pd.DataFrame) -> pd.DataFrame:
    """Only actual linked formal events, ordered by chain stage then time."""
    chain = events.copy()
    chain["_order"] = chain.event_type.map(EVENT_ORDER)
    if chain._order.isna().any():
        raise ValueError("Unexpected formal event type")
    chain = chain.sort_values(["_order", "event_time_utc", "event_id"])
    return chain.drop(columns="_order")


def chain_intervals(events: pd.DataFrame) -> Dict[str, Optional[float]]:
    values: Dict[str, Optional[float]] = {}
    for before, after in [("ARR", "BER"), ("BER", "UNB"), ("UNB", "DEP")]:
        left, right = event_of_type(events, before), event_of_type(events, after)
        values["{}→{} min".format(before, after)] = (
            None if left is None or right is None else
            round((utc(right.event_time_utc) - utc(left.event_time_utc)).total_seconds() / 60, 2)
        )
    anc = events.loc[events.event_type.eq("ANC")].sort_values("event_end_time_utc")
    arr = event_of_type(events, "ARR")
    values["ANC end→ARR min"] = (
        None if anc.empty or arr is None or pd.isna(anc.iloc[-1].event_end_time_utc)
        else round((utc(arr.event_time_utc) - utc(anc.iloc[-1].event_end_time_utc)).total_seconds() / 60, 2)
    )
    return values


def history_at_or_before(ais: pd.DataFrame, replay_time_utc: Any) -> pd.DataFrame:
    """Return only AIS already observed at replay time, including equality."""
    moment = utc(replay_time_utc)
    if ais.empty:
        return ais.copy()
    times = pd.to_datetime(ais["timestamp_utc"], utc=True)
    frame = ais.loc[times <= moment].sort_values("timestamp_utc", kind="stable").copy()
    if not frame.empty and pd.to_datetime(frame.timestamp_utc, utc=True).max() > moment:
        raise AssertionError("Future AIS escaped replay filter")
    return frame


def current_ais_at_or_before(ais_history: pd.DataFrame, replay_time_utc: Any
                             ) -> Tuple[Optional[Dict[str, Any]], Optional[float]]:
    """Latest actual AIS and its age; no position interpolation/extrapolation."""
    if ais_history.empty:
        return None, None
    moment = utc(replay_time_utc)
    row = ais_history.sort_values("timestamp_utc", kind="stable").iloc[-1].to_dict()
    age = (moment - utc(row["timestamp_utc"])).total_seconds() / 60
    if age < 0:
        raise AssertionError("Current AIS is newer than replay time")
    return row, age


def port_status_at_or_before(status_parquet: Path, replay_time_utc: Any
                             ) -> Tuple[Optional[Dict[str, Any]], Optional[float]]:
    """Backward as-of lookup of a frozen 10-minute online-safe snapshot."""
    moment = utc(replay_time_utc)
    with utc_connection() as connection:
        frame = connection.execute(
            "SELECT * FROM read_parquet(?) WHERE snapshot_time_utc <= ? "
            "ORDER BY snapshot_time_utc DESC LIMIT 1",
            [str(status_parquet), moment.to_pydatetime()]).fetchdf()
    if frame.empty:
        return None, None
    row = frame.iloc[0].to_dict()
    age = (moment - utc(row["snapshot_time_utc"])).total_seconds() / 60
    if age < 0:
        raise AssertionError("Future Port Status snapshot selected")
    return row, age


def events_as_replayed(events: pd.DataFrame, replay_time_utc: Any,
                       include_future_truth: bool = False) -> pd.DataFrame:
    """Expose reached events, optionally grey-labelled future ground truth."""
    moment = utc(replay_time_utc)
    chain = event_chain(events)
    times = pd.to_datetime(chain.event_time_utc, utc=True)
    if not include_future_truth:
        chain = chain.loc[times <= moment].copy()
        times = pd.to_datetime(chain.event_time_utc, utc=True)
    chain["replay_status"] = [
        ("future" if stamp > moment else
         "current/reached" if moment - stamp < timedelta(minutes=10) else "past")
        for stamp in times
    ]
    chain["replay_note"] = [
        "Ground truth future event — not available as online input" if status == "future" else ""
        for status in chain.replay_status
    ]
    return chain
