# Raw input → feature-table runbook

Use the project virtual environment exactly as recorded in
`../06_environment/environment.json` and `requirements.lock.txt`. Paths below
assume the original project layout, not a copy of this delivery folder.

All scripts preserve the event-time rule: only AIS available at or before the
prediction snapshot is used. Do not substitute BER time, post-ARR tracks, or
future Port Status values as real-time features.

## Input contracts

| Task | Required source inputs | Final feature table | Port Status status |
|---|---|---|---|
| VAT | AIS, ARR, fixed GeoJSON | `项目\07_high_features_with_split.parquet` | Not used by chosen baseline stacking model; `16b` was an ablation only. |
| Direct/Indirect | AIS, F6 labels, F7 Port Calls, GeoJSON, Port Status | `项目\27_direct_indirect_features_with_port_status.parquet` | Included. Match backwards only. |
| ARR→BER | AIS, F7 Port Calls, GeoJSON, Port Status | `项目\40_arr_to_ber_features_with_split.parquet` | Step 39 is a required bridge into Step 40; Port Status columns are then excluded by final model 49. |

## One-command portable entry points

Every individual step and every entry script accepts exactly one of
`--project-root <folder-containing-项目>` or `--data-dir <项目-folder>`. The
following commands execute feature construction only; they do not train or
overwrite a model.

```powershell
$projectRoot = '<folder-containing-项目>'
& "$projectRoot\项目\.venv\Scripts\python.exe" .\run_vat_feature_pipeline.py --project-root $projectRoot
& "$projectRoot\项目\.venv\Scripts\python.exe" .\run_direct_indirect_feature_pipeline.py --project-root $projectRoot
& "$projectRoot\项目\.venv\Scripts\python.exe" .\run_arr_to_ber_feature_pipeline.py --project-root $projectRoot
```

Append `--dry-run` to print the exact ordered commands without generating
files. The same three entry scripts are copied into this folder as
`run_*_feature_pipeline.py`.

## VAT — high-confidence ARR snapshots and features

Ordered implementation: `04 → 05 → 06 → 06b → 06d → 07`. Run it through the
VAT entry script above, or invoke any copied step with `--data-dir`.

The output is the model feature table used by the final VAT stack. `07` uses
ARR-event grouping, seed `20260917`, and writes `dataset_split`.

## Direct/Indirect — ARR−4h feature table

Ordered implementation: `24 → 25a → 25c → 25d → 25e → 26 → 27`. Run it through
the Direct/Indirect entry script above, or invoke any copied step with
`--data-dir`.

The final classifier consumes `27_direct_indirect_features_with_port_status.parquet`.
The Port Status join is backward-looking, never a future match.

## ARR→BER — immediate pre-ARR feature table

Ordered implementation: `36 → 37 → 38 → 39 → 40`. Run it through the
ARR→BER entry script above, or invoke any copied step with `--data-dir`.

Step 40 reads `39_arr_to_ber_features_with_port_status.parquet`, so Step 39 is
mandatory for the existing reproducible pipeline. This does **not** mean Port
Status enters the selected final model: Step 49 selects the separate no-Port-
Status feature list, and the released model-ready table makes that exact list
explicit. `40` produces the 326-row high+high feature table and its Port
Call-level split.

## Post-generation checks

1. Compare source file hashes with `../06_environment/source_file_manifest.csv`.
2. Verify the generated feature columns and their order against
   `../03_feature_manifests/*feature_order.json`.
3. Regenerate the canonical input-bundle hash by the rule in
   `../06_environment/A_INPUT_HASH_RULE.md`.
4. Invoke the feature-table inference wrapper in `../05_inference` with
   `--input-version` and `--input-hash` set to the new A input bundle.
