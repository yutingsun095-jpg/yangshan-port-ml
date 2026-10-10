# A-side original-function parity report

## Scope and method

The supplied `A_demo_base` was copied intact into this deliverable.  The only
changed A-page file is `A_demo_base/app.py`, where one B prediction panel and a
C JSON download button were inserted.  No A input table, geofence, event,
Port Call, label, Port Status, AIS shard, or A-side loader/helper was modified.

## Preserved A functions

| Original user-visible function | Verification |
|---|---|
| MMSI selector (562 values) and Port Call selector (712 values) | original `list_mmsis` / `list_port_calls` calls retained |
| single-visit AIS trajectory query and replay slider | original bounded `query_visit_ais(mmsi, start, end)` retained |
| current AIS, map auto-view, timeline and event detail | original functions retained |
| ARR, ANC, BER, UNB, DEP formal-event display | original `events_as_replayed` and timeline retained |
| Direct/Indirect A formal label and summary | original label panel retained |
| online-safe Port Status snapshot | original backward as-of `port_status_at_or_before` retained |
| full historical truth chain switch | original switch/timeline behaviour retained; future rows remain labelled |

## Added B-only behaviour

The B panel reads the three precomputed B tables, performs a timestamp filter,
and never writes to A inputs.  It is presented before the original map area so
that the original map, summary, timeline and method sections remain in their
existing order and retain their original behaviour.

No C decision logic is rendered on the A+B page.
