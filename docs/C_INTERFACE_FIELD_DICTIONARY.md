# A+B to C historical-replay interface: field dictionary

The exported JSON is a time-indexed input to C, not a decision result.  Every
record has `interface_version = A_B_C_replay_interface_v1` and always preserves
the distinction between `observed_fact`, `historical_truth`, `model_prediction`,
and `unavailable/not_applicable`.

| Field | Meaning | Source / timing rule |
|---|---|---|
| `port_call_id`, `mmsi` | A-side visit and vessel identity | frozen A Port Call |
| `current_replay_time_utc` | time at which C is replaying the visit | user-selected UTC time |
| `current_a_status` | current observed historical replay stage | only formal events at or before replay time |
| `observed_formal_events` | reached A formal events | `source_type=observed_fact`; never contains future events |
| `vat_prediction` | remaining minutes to A Arrival Gate | frozen B record selected by `event_id=arr_event_id` |
| `direct_indirect_prediction` | Indirect probability and class | frozen B record selected by `port_call_id` |
| `arr_to_ber_prediction` | minutes from ARR to first stable BER | frozen B record selected by `port_call_id` |
| `prediction_time_utc` | historical instant at which B generated the output | must be `<= current_replay_time_utc` |
| `data_available_until_utc` | latest B input data timestamp | must be `<= prediction_time_utc` and replay time |
| `lead_time_min` | elapsed minutes from B prediction generation to the current replay moment | safely knowable context, **not** the source table's held-out target |
| `confidence`, `uncertainty`, `qc_flag` | B-side quality metadata | kept verbatim from frozen output; Direct probability is uncalibrated |
| `model_version`, `model_sha256` | frozen model identity | B output provenance |
| `a_input_version`, `a_input_sha256` | A input identity consumed by B | B output provenance |
| `historical_truth` | deliberately unavailable future truth placeholder | C must not use it as an input |

## Task-specific conditions

- VAT is keyed by ARR `event_id`, because its historical table intentionally
  has an empty `port_call_id` column.  `prediction_lead_time_bin` uses only the
  B predicted value and ASCII labels: `0-1h`, `1-2h`, `2-4h`, `4-6h`, `>6h`.
- Direct/Indirect includes `predicted_class`, `decision_threshold`, and a
  `probability_calibration=uncalibrated` warning.  It is a historical Yangshan
  spatial-replay model, not a portable operational classifier.
- ARR→BER adds `feature_scope=AIS_geo_dynamic_only_no_port_status`; the final
  33-feature model does not use Port Status.  Its long-tail uncertainty is not
  quantitatively calibrated.

## Consumer rules for C

1. Treat `source_type=unavailable/not_applicable` as missing—not zero, Direct,
   or an observed fact.
2. Reject any model record with a prediction/data timestamp later than the
   replay time, or an unacceptable `qc_flag`.
3. Keep A observed facts, B predictions, and C decisions in separate output
   fields.  C must not write a decision back into A events or B prediction files.
4. This interface deliberately contains no JIT, RTA, Fuel, or CO2 calculation.

The B precomputed evaluation tables contain a similarly named source
`lead_time_min` field.  For VAT (and some other evaluation rows) that is a
ground-truth target and could reveal a future event.  The C exporter therefore
does not pass that value through.  Its `lead_time_min` is explicitly the
prediction age at the current replay time, identified by
`lead_time_definition`.
