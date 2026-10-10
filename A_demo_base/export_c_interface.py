"""Export one online-safe A+B replay record for the C module.

Example:
python A_demo_base/export_c_interface.py --port-call-id PC_... \
  --replay-time-utc 2024-01-01T00:00:00Z --output current_input.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from b_runtime_adapter import load_predictions, prediction_bundle, utc_timestamp
from c_interface import build_c_interface_record
from data_loader import get_port_call_bundle
from replay_utils import events_as_replayed


def stage_from_observed_events(events):
    kinds = set(events["event_type"].astype(str))
    if "DEP" in kinds:
        return "已离港"
    if "UNB" in kinds:
        return "已离泊"
    if "BER" in kinds:
        return "已靠泊"
    if "ARR" in kinds:
        return "到港后进港／待靠泊"
    if "ANC" in kinds:
        return "锚泊阶段"
    return "到港前航行"


def main():
    parser = argparse.ArgumentParser(description="Export a frozen A+B replay record for C")
    parser.add_argument("--port-call-id", required=True)
    parser.add_argument("--replay-time-utc", required=True,
                        help="Timezone-aware ISO-8601 timestamp, e.g. 2024-01-01T00:00:00Z")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    replay_time = utc_timestamp(args.replay_time_utc)
    bundle = get_port_call_bundle(args.port_call_id)
    observed = events_as_replayed(bundle["events"], replay_time, include_future_truth=False)
    vat = load_predictions("vat")
    direct = load_predictions("direct_indirect")
    waiting = load_predictions("arr_to_ber")
    predictions = prediction_bundle(bundle["port_call"], replay_time, vat, direct, waiting)
    payload = build_c_interface_record(
        port_call=bundle["port_call"],
        replay_time_utc=replay_time,
        current_a_status=stage_from_observed_events(observed),
        observed_events=observed,
        predictions=predictions,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Wrote online-safe C interface record: {}".format(args.output))


if __name__ == "__main__":
    main()
