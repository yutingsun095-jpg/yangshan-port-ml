from pathlib import Path
from pipeline_paths import resolve_data_dir
import pandas as pd
import duckdb

DATA_DIR = resolve_data_dir(Path(__file__).resolve().parent / "项目")

AIS_FILE = DATA_DIR / "stage2_yangshan_202401.parquet"
LABEL_FILE = (
    DATA_DIR
    / "stage4_yangshan_direct_indirect_labels_202401确定版.parquet"
)
OUTPUT_FILE = DATA_DIR / "24_direct_indirect_snapshots_raw.parquet"

LEAD_HOURS = 4
MAX_LOOKBACK_MINUTES = 30

labels = pd.read_parquet(LABEL_FILE)

labels["mmsi"] = labels["mmsi"].astype(str)
labels["arr_time_utc"] = pd.to_datetime(
    labels["arr_time_utc"],
    utc=True,
)

# 只用 Direct-high / Indirect-high
samples = labels.loc[
    labels["label_confidence"].eq("high")
    & labels["direct_indirect_binary"].notna(),
    [
        "port_call_id",
        "arr_event_id",
        "mmsi",
        "arr_time_utc",
        "direct_indirect_binary",
        "label_confidence",
    ],
].copy()

samples["direct_indirect_binary"] = (
    samples["direct_indirect_binary"].astype(int)
)

samples["target_time_utc"] = (
    samples["arr_time_utc"] - pd.Timedelta(hours=LEAD_HOURS)
)

con = duckdb.connect()
con.execute("PRAGMA enable_progress_bar")
con.register("targets", samples)

query = """
WITH valid_ais AS (
    SELECT
        CAST(mmsi AS VARCHAR) AS mmsi,
        timestamp_utc,
        lon,
        lat,
        sog_knots,
        cog_deg,
        heading_deg,
        nav_status
    FROM read_parquet(?)
    WHERE
        COALESCE(qc_malformed, FALSE) = FALSE
        AND COALESCE(qc_invalid_position, FALSE) = FALSE
        AND COALESCE(qc_duplicate, FALSE) = FALSE
        AND timestamp_utc IS NOT NULL
        AND mmsi IN (SELECT DISTINCT mmsi FROM targets)
)
SELECT
    t.port_call_id,
    t.arr_event_id,
    t.mmsi,
    t.arr_time_utc,
    t.target_time_utc,
    t.direct_indirect_binary,
    t.label_confidence,

    a.timestamp_utc AS sample_time_utc,
    a.lon,
    a.lat,
    a.sog_knots,
    a.cog_deg,
    a.heading_deg,
    a.nav_status
FROM targets AS t
ASOF LEFT JOIN valid_ais AS a
    ON t.mmsi = a.mmsi
    AND t.target_time_utc >= a.timestamp_utc
"""

snapshots = con.execute(query, [str(AIS_FILE)]).df()
con.close()

for column in [
    "arr_time_utc",
    "target_time_utc",
    "sample_time_utc",
]:
    snapshots[column] = pd.to_datetime(
        snapshots[column],
        utc=True,
    )

snapshots["snapshot_lag_minutes"] = (
    snapshots["target_time_utc"]
    - snapshots["sample_time_utc"]
).dt.total_seconds() / 60

snapshots = snapshots.loc[
    snapshots["snapshot_lag_minutes"].between(
        0,
        MAX_LOOKBACK_MINUTES,
    )
].copy()

# 严格防止使用 ARR 后 AIS
if not (snapshots["sample_time_utc"] < snapshots["arr_time_utc"]).all():
    raise ValueError("错误：检测到 ARR 后 AIS。")

print("=== Direct/Indirect 4h Snapshot 结果 ===")
print("保留 Port Call 数：", snapshots["port_call_id"].nunique())
print("保留样本数：", len(snapshots))

print("\n类别分布：")
print(
    snapshots["direct_indirect_binary"]
    .map({0: "Direct (0)", 1: "Indirect (1)"})
    .value_counts()
)

print("\nAIS 滞后分钟：")
print(
    snapshots["snapshot_lag_minutes"]
    .describe()[["min", "50%", "max"]]
)

snapshots.to_parquet(OUTPUT_FILE, index=False)

print(f"\n已保存：{OUTPUT_FILE.name}")
