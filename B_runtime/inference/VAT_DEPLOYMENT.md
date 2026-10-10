# VAT Stacking deployment guide

## Deployed artifact and cadence

- Artifact: `../04_models/vat_stacking_model.joblib`
- Version: `vat_stacking_v1`
- Runtime cadence: produce a per-vessel prediction snapshot every 10 minutes
  when a valid AIS position is available.
- Output: remaining minutes until the fixed Yangshan Arrival Gate is crossed.
  It is not a BER or berth-availability prediction.

## Artifact distribution: GitHub Release asset

The adopted distribution mechanism for the 256,687,482-byte VAT stacking
artifact is **a GitHub Release asset**, not Git LFS and not an unversioned
external drive/object store. Git keeps only code, manifests and checksums;
the binary is attached to release tag `vat_stacking_v1` with this exact name:

```text
vat_stacking_model.joblib
SHA256: f1b89fd5017a74d33f8d7594155c71654bcc9c4a7b1cc35125700f727b2708a4
```

Before first remote deployment, publish that tagged Release and upload this
asset. The release page URL, tag and checksum must be written into the
deployment record. At service startup, download the pinned Release asset once,
verify SHA256 against `../04_models/model_manifest_sha256.csv`, then load it
read-only. Do not load a model whose release tag or checksum differs.

## Runtime flow

```text
raw AIS (MMSI-keyed, event-time ordered)
  → QC / deduplicate / retain only data_available_until_utc
  → last-30-minute vessel state buffer
  → gate + dynamic feature builder (same definitions as scripts 04–07)
  → exact feature-order validation
  → RF + XGBoost + LightGBM base predictions
  → linear stacking meta-model
  → standardized VAT output
```

No observation with timestamp later than `prediction_time_utc` may enter the
feature buffer. Arrival event time is a historical label only; it must never be
an online input. Port Status is not required by the chosen production VAT
model.

## Feature and model contract

The model wrapper takes a completed feature table, not raw AIS. Use the copied
pipeline scripts in `../09_feature_pipelines/VAT/` for online/batch feature
generation. The exact input order is pinned in
`../03_feature_manifests/vat_feature_order.json`; missing required columns are
a hard failure, not an imputation opportunity at deployment time.

Batch inference example, from `05_inference`:

```powershell
python .\infer_vat_from_features.py `
  --input <path-to-vat_live_feature_table.parquet> `
  --output <path-to-vat_predictions.parquet> `
  --input-version yangshan_2024-01_fixed_inputs `
  --input-hash <canonical_A_input_bundle_sha256>
```

## Operational QC

Reject or flag a record if MMSI/time/position/SOG are invalid, the required
30-minute history is unavailable, the GeoJSON gate version differs from the
recorded source hash, or the feature order differs. Recommended output
`qc_flag` values are `ok`, `insufficient_history`, `invalid_position`,
`feature_contract_failed`, and `stale_ais`.

Persist `prediction_time_utc` and `data_available_until_utc` separately in the
production service. In this historical replay they are the same; a live stream
can have an ingestion delay, in which case `data_available_until_utc` is the
latest event-time actually admitted to the feature buffer.

## Deployment limits

This model was assessed on January 2024 Yangshan historical Port Calls and an
ARR-event holdout split. It is a decision-support ETA module, not a safety or
navigation-control system. Monitor live drift in distance-to-gate, SOG,
navigation status and error by lead-time bin before using it operationally.
See `../07_evaluation/GENERALIZATION_SCOPE.md` for its vessel-generalization
limitation.
