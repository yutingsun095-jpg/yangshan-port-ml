# A+B Demo acceptance result

| Requirement | Result |
|---|---|
| Preserve A selectors, AIS bounded query, replay, event chain, labels, Port Status, map and truth toggle | PASS — original functions retained in copied `A_demo_base` |
| VAT join uses `event_id = arr_event_id`, never null VAT `port_call_id` | PASS |
| Direct/Indirect evaluates only high-confidence trainable labels | PASS — Review and not-eligible remain not applicable |
| Direct/Indirect spatial-replay limitation visible | PASS |
| ARR→BER applies only to high/high precomputed model rows and excludes Port Status | PASS |
| No future AIS, event, Port Status or B record used at replay | PASS — filters plus 20-case assertions |
| Future ground truth visibly labelled under complete-chain view | PASS — inherited A behaviour and B truth gating |
| Do not run feature pipelines or B models in Streamlit | PASS — reads frozen prediction Parquet only |
| C output distinguishes fact/prediction/unavailable | PASS — JSON export plus field dictionary/schema |
| Twenty diverse Port Calls tested | PASS — see `TEST_20_PORT_CALL_REPORT.md` |
| No user-specific absolute path / no `__pycache__` in final source | PASS — final packaging scan |
| No normal Git file over 100 MB | PASS — data/models excluded in root `.gitignore`; VAT remains Release Asset only |
