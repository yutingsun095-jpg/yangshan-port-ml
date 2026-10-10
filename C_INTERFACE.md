# C interface entry point

Use the current-replay JSON exported by the A+B page or generated with
`A_demo_base/export_c_interface.py`.  Its full field contract and consumer
rules are in [docs/C_INTERFACE_FIELD_DICTIONARY.md](docs/C_INTERFACE_FIELD_DICTIONARY.md),
with a machine-readable schema in `docs/C_INTERFACE_SCHEMA.json` and one
online-safe example in `examples/C_interface_direct_high_replay.json`.

This package intentionally exports inputs for C but does not implement C's
JIT/RTA/Fuel/CO2 decision logic.  In particular, C must retain the supplied
`source_type` values and must not treat `unavailable/not_applicable` as a fact
or an imputed B prediction.
