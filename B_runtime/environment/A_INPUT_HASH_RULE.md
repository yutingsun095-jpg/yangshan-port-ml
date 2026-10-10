# A-input SHA256 rule

## Purpose

`a_input_sha256` identifies the exact seven-file A-side source bundle used by
B when producing the delivered hold-out outputs. The value is **not** the hash
of a model feature table, a CSV export, or a file modification time.

## File-level rule

For each input file, calculate SHA256 over its unmodified raw bytes, reading
the entire file in binary mode. The resulting digest is recorded in
`source_file_manifest.csv`.

Included inputs: AIS, ARR, BER, Port Call, Direct/Indirect labels, Port
Status, and the fixed GeoJSON. Their current ordered file metadata is in that
manifest; size and modification time are audit fields only and do not enter the
bundle hash.

## Bundle-level canonicalization rule

1. Retain exactly `source_name`, `file_name`, and the file-level `sha256`.
2. Sort records ascending by `source_name`.
3. Serialize that list using UTF-8 JSON with `ensure_ascii=False`,
   `sort_keys=True`, and separators `(',', ':')` (no indentation or trailing
   newline).
4. SHA256 the resulting UTF-8 byte sequence.

Current canonical input-bundle hash:

```text
a232af40c1aebc9cb22a5669b92dea3e1518e70cc0ac63faf2a0775d29261560
```

Canonical JSON payload used for this calculation:

```json
[{"file_name":"stage2_yangshan_202401.parquet","sha256":"3211ac4214e727ca072ce324beb6328016834f9363332cafd5e24345ea257606","source_name":"ais"},{"file_name":"stage3_yangshan_arr_final_202401确定版.csv","sha256":"aa6f8b68f4366cffd6da14e8b0e94a14f2eb40b52dece32d7788c8a86964c718","source_name":"arr"},{"file_name":"stage3_yangshan_ber_final_202401确定版.parquet","sha256":"215c6e14cf3f778b7bc3ed0ff7bf8f8f12ae39e8e791f841b618b08d8dda7c32","source_name":"ber"},{"file_name":"stage4_yangshan_direct_indirect_labels_202401确定版.parquet","sha256":"608f7ff06b7ae17bc2607273eacfc849113a034600f4fe60d5b9ab70f3838249","source_name":"direct_indirect_labels"},{"file_name":"yangshan_geofence_candidate_v2确定版.geojson","sha256":"005eff6f9427b49f81b1167ef089146f418dc1189c6c2331f717a299ff826855","source_name":"geofence"},{"file_name":"stage4_yangshan_port_calls_final_202401确定版.parquet","sha256":"19eee41dc5a751cfec703707183cffc426a671424e25520f6a584a1515efb031","source_name":"port_calls"},{"file_name":"stage5_yangshan_port_status_10min_202401确定版.parquet","sha256":"49780a1e2b3d191b545e32ca5bb3555c69ec3abbdf6a59893f2574609a06685c","source_name":"port_status"}]
```

If any included source byte changes, recompute every file hash and this bundle
hash before running feature generation or inference. For a newly received A
bundle, pass its recalculated identifier through `--input-hash`; do not reuse
the value above.
