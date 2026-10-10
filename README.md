# Yangshan A+B historical replay Demo

This deliverable adds frozen B-model historical predictions to the supplied A
historical AIS replay Demo.  It is deliberately a **replay** application, not
a production ETA or port-operation deployment.

## What stays unchanged

- `A_demo_base/data/` contains the frozen A-side formal events, Port Calls,
  labels, Port Status, GeoJSON, and clipped AIS query shards.  The application
  opens them read-only.
- The three B tasks use only the supplied precomputed records in
  `B_runtime/predictions/`.  The page does not retrain a model, invoke the
  256 MB VAT model, or run feature engineering.
- A-side historical facts, B-side predictions, and C-side downstream inputs
  are visibly and structurally separated.

## Run locally

Use Python 3.10–3.12 in a fresh virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r A_demo_base\requirements.txt
streamlit run A_demo_base\app.py
```

Open the local address reported by Streamlit.  Do not start the page by running
`app.py` with plain Python.

## B prediction semantics

| Task | Key used to attach to A | What the UI shows |
|---|---|---|
| VAT | `event_id = arr_event_id` | predicted remaining minutes to ARR, truth only once ARR is reached |
| Direct/Indirect | `port_call_id` | predicted class and Indirect probability; only high-confidence trainable labels are evaluated |
| ARR→BER | `port_call_id` | predicted ARR-to-first-stable-BER minutes; truth only once BER is reached |

The page selects a B record only when both `prediction_time_utc` and
`data_available_until_utc` are no later than the current replay time.  When
the full historical-truth switch is on, unreached future truth remains labelled
`历史真值 · 当时尚未知` and never becomes an online model input.

## C handoff

The button in the **B模型预测 · 历史回放** panel downloads an online-safe
JSON record for the current replay moment.  The same export can be created
without starting Streamlit:

```powershell
python A_demo_base\export_c_interface.py `
  --port-call-id PC_202401_414688000_20240101T021420 `
  --replay-time-utc 2024-01-01T02:14:30Z `
  --output C_interface_example.json
```

See [the C field dictionary](docs/C_INTERFACE_FIELD_DICTIONARY.md) and
`C_INTERFACE_SCHEMA.json`.  C must consume this as an input contract only;
JIT/RTA/Fuel/CO2 decision logic is intentionally out of scope here.

## VAT model deployment boundary

This Demo does **not** include the large VAT model.  If a separate real-time
inference service is needed, obtain the frozen model only from the documented
GitHub Release Asset, verify its SHA256 before loading it, and keep that
separate environment outside this Streamlit replay app.  The replay UI uses
the packaged `vat_reproduced.parquet` instead.

## Verification and reports

- [20-Port-Call test report](docs/TEST_20_PORT_CALL_REPORT.md)
- [A-side original-function parity report](docs/A_ORIGINAL_FUNCTIONAL_PARITY_REPORT.md)
- [A/B time-causality report](docs/A_B_TIME_CAUSALITY_CHECK_REPORT.md)
- [C interface field dictionary](docs/C_INTERFACE_FIELD_DICTIONARY.md)

The package is designed to be run from any directory.  No source file relies
on a user-specific absolute path.
