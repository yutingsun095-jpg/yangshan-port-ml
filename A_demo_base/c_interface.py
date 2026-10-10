"""Online-safe A+B replay interface record for the downstream C module.

This is intentionally a data contract, not C decision logic.  It contains only
facts reached by the supplied replay time and B records available by that time.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

import pandas as pd

from b_runtime_adapter import lead_time_bin_ascii, record_as_serializable, utc_timestamp


INTERFACE_VERSION = "A_B_C_replay_interface_v1"


def _iso(value: Any) -> Optional[str]:
    if value is None or pd.isna(value):
        return None
    return utc_timestamp(value).isoformat()


def _float(value: Any) -> Optional[float]:
    return None if value is None or pd.isna(value) else float(value)


def _prediction_payload(row: Optional[Dict[str, Any]], *, kind: str,
                        replay_time_utc: Any,
                        extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if row is None:
        return {
            "source_type": "unavailable/not_applicable",
            "reason": "no_frozen_prediction_available_at_replay_time",
            "task": kind,
        }
    replay_time = utc_timestamp(replay_time_utc)
    prediction_time = utc_timestamp(row["prediction_time_utc"])
    payload = {
        "source_type": "model_prediction",
        "task": row["task"],
        "model_version": row["model_version"],
        "prediction": _float(row["prediction"]),
        "unit": row["unit"],
        "prediction_time_utc": _iso(prediction_time),
        "data_available_until_utc": _iso(row["data_available_until_utc"]),
        # The source table's lead_time_min is an offline evaluation target for
        # some tasks.  Never expose it to C before the target event happens.
        # This exported lead time is the safely knowable prediction age.
        "lead_time_min": round((replay_time - prediction_time).total_seconds() / 60, 6),
        "lead_time_definition": "elapsed_minutes_from_prediction_generation_to_current_replay",
        "confidence": _float(row.get("confidence")),
        "uncertainty": None if pd.isna(row.get("uncertainty")) else row.get("uncertainty"),
        "qc_flag": row.get("qc_flag"),
        "a_input_version": row.get("a_input_version"),
        "a_input_sha256": row.get("a_input_sha256"),
        "model_sha256": row.get("model_sha256"),
    }
    if extra:
        payload.update(extra)
    return payload


def build_c_interface_record(*, port_call: Dict[str, Any], replay_time_utc: Any,
                             current_a_status: str, observed_events: pd.DataFrame,
                             predictions: Dict[str, Optional[Dict[str, Any]]]) -> Dict[str, Any]:
    """Build a single C-ready record; never emit future ground truth as an input."""
    replay_time = utc_timestamp(replay_time_utc)
    events = []
    for event in observed_events.itertuples(index=False):
        event_time = utc_timestamp(event.event_time_utc)
        if event_time > replay_time:
            raise AssertionError("C interface attempted to expose a future event")
        events.append({
            "source_type": "observed_fact",
            "event_id": str(event.event_id),
            "event_type": str(event.event_type),
            "event_time_utc": event_time.isoformat(),
            "confidence_level": str(event.confidence_level),
            "qc_flag": str(event.qc_flag),
        })

    vat = _prediction_payload(predictions.get("vat"), kind="VAT", replay_time_utc=replay_time)
    if vat["source_type"] == "model_prediction":
        vat["prediction_lead_time_bin"] = lead_time_bin_ascii(vat["prediction"])

    direct = _prediction_payload(predictions.get("direct_indirect"), kind="DIRECT_INDIRECT",
                                 replay_time_utc=replay_time)
    if direct["source_type"] == "model_prediction":
        source = predictions["direct_indirect"]
        direct["predicted_class"] = source.get("predicted_class")
        direct["decision_threshold"] = _float(source.get("decision_threshold"))
        direct["probability_calibration"] = "uncalibrated"
        direct["scope_note"] = "historical_yangshan_replay_with_strong_spatial_features"

    waiting = _prediction_payload(predictions.get("arr_to_ber"), kind="ARR_TO_BER",
                                  replay_time_utc=replay_time)
    if waiting["source_type"] == "model_prediction":
        waiting["feature_scope"] = "AIS_geo_dynamic_only_no_port_status"
        waiting["uncertainty_note"] = "long_tail_uncertainty_not_quantified"

    return {
        "interface_version": INTERFACE_VERSION,
        "record_type": "A_B_historical_replay_input_for_C",
        "replay_mode_note": "historical replay prediction; not real-time production output",
        "port_call_id": str(port_call["port_call_id"]),
        "mmsi": str(port_call["mmsi"]),
        "current_replay_time_utc": replay_time.isoformat(),
        "current_a_status": {"source_type": "observed_fact", "value": current_a_status},
        "observed_formal_events": events,
        "vat_prediction": vat,
        "direct_indirect_prediction": direct,
        "arr_to_ber_prediction": waiting,
        "historical_truth": {
            "source_type": "unavailable/not_applicable",
            "note": "Future historical truth is intentionally omitted from the C input contract.",
        },
    }
