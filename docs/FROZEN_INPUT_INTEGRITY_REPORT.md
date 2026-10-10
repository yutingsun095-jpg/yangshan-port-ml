# Frozen-input integrity verification

Before integration, the original handoff's `A_demo_base/data/` and
`B_runtime/` were copied to this new output directory.  After all A+B Demo
changes and tests, SHA256 was calculated separately for every corresponding
file in the original handoff and in this delivery.

| Scope | File count | Result |
|---|---:|---|
| A formal data: clipped AIS, Port Calls, labels, Port Status, events, GeoJSON and manifest | 7 | all SHA256 pairs identical |
| Frozen B runtime model artifacts, predictions, feature manifests, pipelines, inference and evaluation artifacts | 66 | all SHA256 pairs identical |

The only edited or newly generated materials are outside those frozen input
directories: the copied Streamlit page integration, its read-only B/C adapter,
documentation, verification output, screenshots, and packaging metadata.  The
three B portability metadata/doc files (`environment/environment.json`,
`feature_pipelines/RUNBOOK.md`, `inference/VAT_DEPLOYMENT.md`) were also
sanitized to replace a user-specific path with placeholders; no executable
logic, model, feature contract, prediction, or A-input hash changed.

This confirms that no A-side formal event, Port Call, Port Status, GeoJSON or
AIS file was modified and no B model/prediction/feature contract was changed.
