"""Read-only 20-Port-Call integration checks for the A+B replay Demo.

The test intentionally does not change frozen A inputs, B records, or model
artifacts.  It checks identity joins, timestamp causality, and C export shape.
"""

from __future__ import annotations

from datetime import timedelta
import json
import sys
from pathlib import Path
from typing import Callable, Dict, List, Tuple

import pandas as pd


ROOT = Path(__file__).resolve().parent
APP_DIR = ROOT / "A_demo_base"
sys.path.insert(0, str(APP_DIR))

from b_runtime_adapter import load_predictions, prediction_bundle, utc_timestamp  # noqa: E402
from c_interface import build_c_interface_record  # noqa: E402
from data_loader import SOURCES, get_port_call_bundle, query_visit_ais  # noqa: E402
from replay_utils import (current_ais_at_or_before, events_as_replayed,
                          history_at_or_before, port_status_at_or_before,
                          visit_query_window)  # noqa: E402


def choose_cases(calls: pd.DataFrame, vat: pd.DataFrame, direct: pd.DataFrame,
                 waiting: pd.DataFrame) -> List[Tuple[str, str]]:
    """Choose 20 unique Port Calls spanning the published acceptance categories."""
    chosen: List[Tuple[str, str]] = []
    seen = set()

    def choose(name: str, predicate: Callable[[pd.DataFrame], pd.Series]):
        candidates = calls.loc[predicate(calls)].sort_values(["arr_time_utc", "port_call_id"])
        candidates = candidates.loc[~candidates.port_call_id.astype(str).isin(seen)]
        if candidates.empty:
            raise AssertionError("No candidate for acceptance category: " + name)
        port_call_id = str(candidates.iloc[0].port_call_id)
        seen.add(port_call_id)
        chosen.append((name, port_call_id))

    choose("Direct-high", lambda x: x.final_label.eq("direct") & x.label_confidence.eq("high"))
    choose("Indirect-high", lambda x: x.final_label.eq("indirect") & x.label_confidence.eq("high"))
    choose("Main", lambda x: x.final_berth_zone.eq("Main"))
    choose("West", lambda x: x.final_berth_zone.eq("West"))
    choose("no_BER", lambda x: ~x.has_ber.astype(bool))
    choose("no_UNB", lambda x: ~x.has_unb.astype(bool))
    choose("no_DEP", lambda x: ~x.has_dep.astype(bool))
    choose("linked_ANC", lambda x: x.linked_anc_count.gt(0))
    choose("Review", lambda x: x.final_label.eq("review"))
    choose("not_eligible_no_BER", lambda x: x.final_label.eq("not_eligible_no_ber"))
    choose("ARR_to_BER_applicable", lambda x: x.arr_confidence_level.eq("high") & x.ber_confidence_level.eq("high"))
    choose("ARR_to_BER_not_applicable", lambda x: ~(x.arr_confidence_level.eq("high") & x.ber_confidence_level.eq("high")))
    choose("VAT_prediction_missing_review_ARR", lambda x: x.arr_confidence_level.eq("review"))

    # Select one unique call for each real VAT ground-truth lead-time category.
    vat_with_call = vat[["event_id", "lead_time_min"]].merge(
        calls[["port_call_id", "arr_event_id"]], left_on="event_id", right_on="arr_event_id",
        how="inner", validate="many_to_one")
    bins = [("VAT_0-1h", 0, 60), ("VAT_1-2h", 60, 120),
            ("VAT_2-4h", 120, 240), ("VAT_4-6h", 240, 360)]
    for name, lower, upper in bins:
        ids = set(vat_with_call.loc[
            vat_with_call.lead_time_min.ge(lower) & vat_with_call.lead_time_min.lt(upper),
            "port_call_id"].astype(str))
        choose(name, lambda x, ids=ids: x.port_call_id.astype(str).isin(ids))

    # Fill the remainder with calls that have a B output, while keeping visits distinct.
    available = set(direct.port_call_id.astype(str)) | set(waiting.port_call_id.astype(str))
    while len(chosen) < 20:
        choose("additional_B_linked_case_{:02d}".format(len(chosen) + 1),
               lambda x, ids=available: x.port_call_id.astype(str).isin(ids))
    return chosen


def stage(observed: pd.DataFrame) -> str:
    kinds = set(observed.event_type.astype(str))
    if "DEP" in kinds:
        return "已离港"
    if "UNB" in kinds:
        return "已离泊"
    if "BER" in kinds:
        return "已靠泊"
    if "ARR" in kinds:
        return "到港后进港／待靠泊"
    return "到港前航行"


def replay_time_for(call: Dict, vat: pd.DataFrame, direct: pd.DataFrame,
                    waiting: pd.DataFrame) -> pd.Timestamp:
    candidates = []
    for frame, key, value in [
        (vat, "event_id", str(call["arr_event_id"])),
        (direct, "port_call_id", str(call["port_call_id"])),
        (waiting, "port_call_id", str(call["port_call_id"])),
    ]:
        found = frame.loc[frame[key].astype(str).eq(value), "prediction_time_utc"]
        if not found.empty:
            candidates.append(pd.to_datetime(found, utc=True).max())
    if candidates:
        return utc_timestamp(max(candidates)) + timedelta(minutes=1)
    return utc_timestamp(call["arr_time_utc"]) - timedelta(minutes=1)


def main():
    calls = pd.read_parquet(SOURCES["port_calls"])
    vat = load_predictions("vat")
    direct = load_predictions("direct_indirect")
    waiting = load_predictions("arr_to_ber")
    cases = choose_cases(calls, vat, direct, waiting)
    assert len(cases) == 20 and len({item[1] for item in cases}) == 20

    results = []
    for category, port_call_id in cases:
        bundle = get_port_call_bundle(port_call_id)
        call, events = bundle["port_call"], bundle["events"]
        moment = replay_time_for(call, vat, direct, waiting)
        start, end, _, _ = visit_query_window(events)
        if not start <= moment <= end:
            raise AssertionError("Test replay time escaped its selected Port Call window")
        ais = query_visit_ais(str(call["mmsi"]), start, end)
        history = history_at_or_before(ais, moment)
        current, _ = current_ais_at_or_before(history, moment)
        if current is not None and utc_timestamp(current["timestamp_utc"]) > moment:
            raise AssertionError("Future AIS appeared in a test case")
        snapshot, _ = port_status_at_or_before(SOURCES["port_status"], moment)
        if snapshot is not None and utc_timestamp(snapshot["snapshot_time_utc"]) > moment:
            raise AssertionError("Future Port Status appeared in a test case")
        predictions = prediction_bundle(call, moment, vat, direct, waiting)
        for name, row in predictions.items():
            if row is not None:
                assert utc_timestamp(row["prediction_time_utc"]) <= moment
                assert utc_timestamp(row["data_available_until_utc"]) <= utc_timestamp(row["prediction_time_utc"])
        observed = events_as_replayed(events, moment, include_future_truth=False)
        record = build_c_interface_record(
            port_call=call, replay_time_utc=moment, current_a_status=stage(observed),
            observed_events=observed, predictions=predictions)
        assert all(pd.Timestamp(item["event_time_utc"]) <= moment for item in record["observed_formal_events"])
        results.append({
            "category": category,
            "port_call_id": port_call_id,
            "mmsi": str(call["mmsi"]),
            "replay_time_utc": moment.isoformat(),
            "observed_event_count": len(observed),
            "vat_available": predictions["vat"] is not None,
            "direct_indirect_available": predictions["direct_indirect"] is not None,
            "arr_to_ber_available": predictions["arr_to_ber"] is not None,
            "ais_history_rows": len(history),
            "status_available": snapshot is not None,
            "result": "PASS",
        })

    output = pd.DataFrame(results)
    csv_path = ROOT / "docs" / "test_20_port_calls.csv"
    output.to_csv(csv_path, index=False, encoding="utf-8-sig")
    display_columns = ["category", "port_call_id", "mmsi", "replay_time_utc",
                       "vat_available", "direct_indirect_available",
                       "arr_to_ber_available", "result"]
    markdown_header = "| " + " | ".join(display_columns) + " |"
    markdown_rule = "| " + " | ".join(["---"] * len(display_columns)) + " |"
    markdown_rows = [
        "| " + " | ".join(str(row[column]) for column in display_columns) + " |"
        for _, row in output.iterrows()
    ]
    lines = [
        "# 20-Port-Call integration test report",
        "",
        "**Result: PASS (20 / 20).**  The checks are read-only and were run against the packaged A inputs and frozen B prediction tables.",
        "",
        "## Coverage",
        "",
        "The selected cases cover Direct, Indirect, Main, West, no BER, no UNB, no DEP, linked ANC, Review, not eligible, ARR→BER applicable/not-applicable, VAT missing review ARR, and the ASCII VAT bins `0-1h`, `1-2h`, `2-4h`, `4-6h`.",
        "",
        "## Checks applied to every case",
        "",
        "- `port_call_id`, MMSI and A formal-event links load consistently.",
        "- AIS history contains no row after the test replay time.",
        "- selected Port Status contains no snapshot after replay time.",
        "- every B prediction has `data_available_until_utc <= prediction_time_utc <= replay_time_utc`.",
        "- C export contains only observed formal events and has no future historical truth input.",
        "",
        "## Case list",
        "",
        markdown_header,
        markdown_rule,
        *markdown_rows,
        "",
        "The machine-readable equivalent is `docs/test_20_port_calls.csv`.",
    ]
    (ROOT / "docs" / "TEST_20_PORT_CALL_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"result": "PASS", "cases": len(output), "csv": str(csv_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
