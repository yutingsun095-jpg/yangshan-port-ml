# Final engineering run confirmation

## A+B historical replay application

- Streamlit application started successfully and returned HTTP 200 locally.
- A rendered page was checked after loading the selected A Port Call.
- The B panel displays VAT, Direct/Indirect and ARR→BER cards, the explicit
  historical-replay warning, and the C JSON export control.
- The A selector, replay control, A Port Status panel, current vessel state,
  map and timeline remain in the copied A page; no frozen A input file changed.

## Portable feature-pipeline entries

All three copied task-specific entry points completed a `--data-dir ...
--dry-run` confirmation.  The output paths resolve B source code relative to
`B_runtime/feature_pipelines/`, while the supplied data parameter is used only
to locate data.

| Task | Ordered feature steps confirmed |
|---|---|
| VAT | 04 → 05 → 06 → 06b → 06d → 07 |
| Direct/Indirect | 24 → 25a → 25c → 25d → 25e → 26 → 27 |
| ARR→BER | 36 → 37 → 38 → 39 → 40 |

The legacy `run_feature_pipeline.py` is a compatibility forwarder to the same
`pipeline_runner.py`; it does not introduce a second implementation.

## ARR→BER final-model boundary

The frozen `arr_to_ber_final_feature_manifest.json` confirms 32 numeric
features plus one categorical `nav_status` feature: **33 model features** in
total.  Its 19 Port Status fields are explicitly listed as excluded.  The UI
and C interface preserve this as `AIS_geo_dynamic_only_no_port_status`; the
Demo never re-adds Port Status to that final model.
