# A/B historical replay time-causality report

## Enforced rules

1. A-side AIS is queried per selected MMSI and bounded visit window; its
   displayed history is filtered to `timestamp_utc <= replay_time_utc`.
2. A Port Status is a backward as-of lookup, with
   `snapshot_time_utc <= replay_time_utc`.
3. A formal event is visible by default only when
   `event_time_utc <= replay_time_utc`.  The optional complete-chain view gives
   later records `历史真值 · 当时尚未知` and does not feed them to B or C.
4. A B prediction is eligible only when both
   `prediction_time_utc <= replay_time_utc` and
   `data_available_until_utc <= prediction_time_utc <= replay_time_utc`.
5. VAT actual target/error is hidden until ARR; ARR→BER actual target/error and
   BER confidence are hidden until BER.  Direct/Indirect A truth/correctness is
   hidden until BER.  The full-truth switch can reveal them solely with the
   explicit future-truth label.
6. The C export is stricter than the UI: it always uses the observed-only event
   chain and exports no future historical truth.

## B model feature boundary

The page never invokes B feature engineering or a model.  It does not query
future AIS and does not add Port Status to the frozen ARR→BER model.  The
ARR→BER precomputed model identity is
`arr_to_ber_rf_log1p_no_port_status_v1` and its exported feature scope is
`AIS_geo_dynamic_only_no_port_status`.

## Validation assertion points

The integrated code asserts that a current AIS point, selected Port Status row,
and any selected B record cannot exceed replay time.  The test runner executes
the same checks over 20 different Port Calls and writes the result to
`docs/test_20_port_calls.csv`.
