"""Read-only adapter for the frozen B historical prediction tables.

This module deliberately does not load a model, rebuild features, or query AIS.
It only selects a precomputed B prediction whose prediction and data-availability
times are no later than the replay time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd


APP_DIR = Path(__file__).resolve().parent
PREDICTIONS_DIR = APP_DIR.parent / "B_runtime" / "predictions"
PREDICTION_FILES = {
    "vat": PREDICTIONS_DIR / "vat_reproduced.parquet",
    "direct_indirect": PREDICTIONS_DIR / "direct_indirect_reproduced.parquet",
    "arr_to_ber": PREDICTIONS_DIR / "arr_to_ber_reproduced.parquet",
}
REQUIRED_COLUMNS = {
    "task", "model_version", "prediction_time_utc", "data_available_until_utc",
    "event_id", "port_call_id", "prediction", "unit", "lead_time_min",
    "confidence", "uncertainty", "qc_flag", "a_input_version",
    "a_input_sha256", "model_sha256",
}


def utc_timestamp(value: Any) -> pd.Timestamp:
    """Return a timezone-aware UTC Timestamp."""
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        raise ValueError("A non-null timestamp is required")
    if stamp.tzinfo is None:
        raise ValueError("A timezone-aware UTC timestamp is required")
    return stamp.tz_convert("UTC")


def load_predictions(kind: str) -> pd.DataFrame:
    """Load one small frozen historical-prediction table with time validation."""
    if kind not in PREDICTION_FILES:
        raise KeyError("Unknown B prediction kind: " + kind)
    path = PREDICTION_FILES[kind]
    if not path.is_file():
        raise FileNotFoundError("Missing frozen B prediction table: " + str(path))
    frame = pd.read_parquet(path)
    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError("B prediction table missing columns: " + ", ".join(sorted(missing)))
    for column in ["prediction_time_utc", "data_available_until_utc"]:
        frame[column] = pd.to_datetime(frame[column], utc=True)
    if frame["prediction_time_utc"].isna().any() or frame["data_available_until_utc"].isna().any():
        raise ValueError("B prediction timestamps must not be null")
    if (frame["data_available_until_utc"] > frame["prediction_time_utc"]).any():
        raise ValueError("A B prediction uses data reported after its prediction time")
    return frame


def _available_rows(frame: pd.DataFrame, replay_time_utc: Any) -> pd.DataFrame:
    """Strictly retain predictions reproducibly available at the replay time."""
    replay_time = utc_timestamp(replay_time_utc)
    subset = frame.loc[
        (frame["prediction_time_utc"] <= replay_time)
        & (frame["data_available_until_utc"] <= replay_time)
        & (frame["data_available_until_utc"] <= frame["prediction_time_utc"])
    ].copy()
    if not subset.empty and (
        (subset["prediction_time_utc"] > replay_time).any()
        or (subset["data_available_until_utc"] > replay_time).any()
    ):
        raise AssertionError("Future B prediction escaped replay-time filtering")
    return subset


def _latest_row(rows: pd.DataFrame) -> Optional[Dict[str, Any]]:
    if rows.empty:
        return None
    row = rows.sort_values(
        ["prediction_time_utc", "data_available_until_utc"], kind="stable"
    ).iloc[-1].to_dict()
    return row


def select_vat_prediction(vat: pd.DataFrame, arr_event_id: str,
                          replay_time_utc: Any) -> Optional[Dict[str, Any]]:
    """Select VAT by ARR event ID; its precomputed table intentionally has no PC ID."""
    rows = vat.loc[vat["event_id"].astype(str).eq(str(arr_event_id))]
    return _latest_row(_available_rows(rows, replay_time_utc))


def select_port_call_prediction(frame: pd.DataFrame, port_call_id: str,
                                replay_time_utc: Any) -> Optional[Dict[str, Any]]:
    """Select one frozen B prediction attached to an A Port Call."""
    rows = frame.loc[frame["port_call_id"].astype(str).eq(str(port_call_id))]
    return _latest_row(_available_rows(rows, replay_time_utc))


def record_as_serializable(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Convert a selected record to JSON-friendly primitives without dropping provenance."""
    if row is None:
        return None
    result: Dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, pd.Timestamp):
            result[key] = value.isoformat()
        elif pd.isna(value):
            result[key] = None
        elif hasattr(value, "item"):
            result[key] = value.item()
        else:
            result[key] = value
    return result


def lead_time_bin_ascii(minutes: Optional[float]) -> Optional[str]:
    """Return the frozen Demo's ASCII lead-time label, never a Unicode dash."""
    if minutes is None or pd.isna(minutes):
        return None
    value = float(minutes)
    if value < 60:
        return "0-1h"
    if value < 120:
        return "1-2h"
    if value < 240:
        return "2-4h"
    if value < 360:
        return "4-6h"
    return ">6h"


def prediction_bundle(port_call: Dict[str, Any], replay_time_utc: Any,
                      vat: pd.DataFrame, direct_indirect: pd.DataFrame,
                      arr_to_ber: pd.DataFrame) -> Dict[str, Optional[Dict[str, Any]]]:
    """Return only B records permissible at the specified replay instant."""
    return {
        "vat": select_vat_prediction(vat, str(port_call["arr_event_id"]), replay_time_utc),
        "direct_indirect": select_port_call_prediction(
            direct_indirect, str(port_call["port_call_id"]), replay_time_utc),
        "arr_to_ber": select_port_call_prediction(
            arr_to_ber, str(port_call["port_call_id"]), replay_time_utc),
    }
