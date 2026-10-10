from pathlib import Path
from pipeline_paths import resolve_data_dir
import json
import pandas as pd

DATA_DIR = resolve_data_dir(Path(__file__).resolve().parent / "项目")

INPUT_FILE = DATA_DIR / "26_direct_indirect_features_with_split.parquet"
PORT_STATUS_FILE = (
    DATA_DIR
    / "stage5_yangshan_port_status_10min_202401确定版.parquet"
)
OUTPUT_FILE = DATA_DIR / "27_direct_indirect_features_with_port_status.parquet"
MANIFEST_FILE = DATA_DIR / "27_direct_indirect_port_status_manifest.json"

MAX_STATUS_LAG_MINUTES = 10

snapshots = pd.read_parquet(INPUT_FILE)
port_status = pd.read_parquet(PORT_STATUS_FILE)

snapshots["sample_time_utc"] = (
    pd.to_datetime(
        snapshots["sample_time_utc"],
        utc=True,
    )
    .astype("datetime64[ns, UTC]")
)

port_status["snapshot_time_utc"] = (
    pd.to_datetime(
        port_status["snapshot_time_utc"],
        utc=True,
    )
    .astype("datetime64[ns, UTC]")
)

# 当 approach_vessel_count = 0 时，速度均值/中位数的 NaN
# 表示“没有接近区船舶”，因此填为 0；不是用未来数据填补。
for column in ["approach_mean_sog", "approach_median_sog"]:
    port_status.loc[
        port_status["approach_vessel_count"].eq(0),
        column,
    ] = 0.0

snapshots = snapshots.sort_values("sample_time_utc")
port_status = port_status.sort_values("snapshot_time_utc")

merged = pd.merge_asof(
    snapshots,
    port_status,
    left_on="sample_time_utc",
    right_on="snapshot_time_utc",
    direction="backward",
    tolerance=pd.Timedelta(minutes=MAX_STATUS_LAG_MINUTES),
)

# 严格防止未来信息
matched = merged["snapshot_time_utc"].notna()

if (
    merged.loc[matched, "snapshot_time_utc"]
    > merged.loc[matched, "sample_time_utc"]
).any():
    raise ValueError("错误：发现未来 Port Status 被匹配。")

merged["port_status_lag_minutes"] = (
    merged["sample_time_utc"]
    - merged["snapshot_time_utc"]
).dt.total_seconds() / 60

# 保留环境特征；total occupied 与 main/west occupied 完全重复，故不纳入模型。
port_status_features = [
    "active_vessel_count",
    "approach_vessel_count",
    "approach_mean_sog",
    "approach_median_sog",
    "anchorage_present_count",
    "anchorage_confirmed_waiting_count",
    "berth_main_present_count",
    "berth_west_present_count",
    "berth_main_confirmed_occupied_count",
    "berth_west_confirmed_occupied_count",
    "observed_inbound_gate_crossings_30m",
    "observed_inbound_gate_crossings_60m",
    "observed_inbound_gate_crossings_120m",
    "observed_outbound_gate_crossings_30m",
    "observed_outbound_gate_crossings_60m",
    "observed_outbound_gate_crossings_120m",
    "unique_inbound_vessels_60m",
    "unique_outbound_vessels_60m",
]

print("=== Port Status 匹配审计 ===")
print("总 snapshot 数：", len(merged))
print("成功匹配数：", int(matched.sum()))
print("匹配覆盖率：", f"{matched.mean() * 100:.2f}%")

print("\n各集合匹配覆盖率：")
print(
    merged.groupby("dataset_split")["snapshot_time_utc"]
    .apply(lambda x: x.notna().mean() * 100)
    .round(2)
)

print("\nPort Status 滞后分钟：")
print(
    merged.loc[matched, "port_status_lag_minutes"]
    .describe()[["min", "50%", "max"]]
)

print("\n模型环境特征缺失数：")
print(
    merged[port_status_features]
    .isna()
    .sum()
    .sort_values(ascending=False)
)

merged.to_parquet(OUTPUT_FILE, index=False)

manifest = {
    "matching_rule": (
        "Backward as-of match: snapshot_time_utc <= sample_time_utc; "
        f"maximum lag {MAX_STATUS_LAG_MINUTES} minutes."
    ),
    "port_status_features": port_status_features,
    "excluded_columns": [
        "snapshot_time_utc",
        "port_status_lag_minutes",
        "berth_total_confirmed_occupied_count",
        "active_ais_max_age_min",
        "active_ais_median_age_min",
    ],
}

with MANIFEST_FILE.open("w", encoding="utf-8") as f:
    json.dump(manifest, f, ensure_ascii=False, indent=2)

print(f"\n已保存增强数据：{OUTPUT_FILE.name}")
print(f"已保存环境特征清单：{MANIFEST_FILE.name}")
