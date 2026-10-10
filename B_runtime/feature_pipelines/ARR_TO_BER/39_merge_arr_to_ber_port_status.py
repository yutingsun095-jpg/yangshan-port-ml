from pathlib import Path
from pipeline_paths import resolve_data_dir
import pandas as pd

DATA_DIR = resolve_data_dir(Path(__file__).resolve().parent / "项目")

INPUT_FILE = DATA_DIR / "38_arr_to_ber_features_dynamic.parquet"
PORT_STATUS_FILE = DATA_DIR / "stage5_yangshan_port_status_10min_202401确定版.parquet"
OUTPUT_FILE = DATA_DIR / "39_arr_to_ber_features_with_port_status.parquet"

TIME_DTYPE = "datetime64[ns, UTC]"
MAX_STATUS_LAG_MINUTES = 10

snapshots = pd.read_parquet(INPUT_FILE)
port_status = pd.read_parquet(PORT_STATUS_FILE)

snapshots["sample_time_utc"] = pd.to_datetime(
    snapshots["sample_time_utc"], utc=True
).astype(TIME_DTYPE)

port_status["snapshot_time_utc"] = pd.to_datetime(
    port_status["snapshot_time_utc"], utc=True
).astype(TIME_DTYPE)

port_status = port_status.rename(
    columns={"snapshot_time_utc": "port_status_time_utc"}
)

# 保留实时可见的港口状态；不使用 active_ais_*_age_min 这类数据质量字段。
status_features = [
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
    "berth_total_confirmed_occupied_count",
    "observed_inbound_gate_crossings_30m",
    "observed_inbound_gate_crossings_60m",
    "observed_inbound_gate_crossings_120m",
    "observed_outbound_gate_crossings_30m",
    "observed_outbound_gate_crossings_60m",
    "observed_outbound_gate_crossings_120m",
    "unique_inbound_vessels_60m",
    "unique_outbound_vessels_60m",
]

missing = [c for c in status_features if c not in port_status.columns]
if missing:
    raise KeyError(f"Port Status 缺少字段：{missing}")

print("=== 输入数据 ===")
print("ARR→BER snapshot 数：", len(snapshots))
print("Port Status 行数：", len(port_status))
print(
    "Port Status 时间范围：",
    port_status["port_status_time_utc"].min(),
    "至",
    port_status["port_status_time_utc"].max(),
)

# merge_asof 前按时间排序、重置索引。
left = snapshots.sort_values("sample_time_utc").reset_index(drop=True)
right = (
    port_status[["port_status_time_utc"] + status_features]
    .sort_values("port_status_time_utc")
    .reset_index(drop=True)
)

# 严格向后匹配：状态时刻 <= 预测时刻。
merged = pd.merge_asof(
    left,
    right,
    left_on="sample_time_utc",
    right_on="port_status_time_utc",
    direction="backward",
    allow_exact_matches=True,
    tolerance=pd.Timedelta(minutes=MAX_STATUS_LAG_MINUTES),
)

merged["port_status_lag_minutes"] = (
    merged["sample_time_utc"] - merged["port_status_time_utc"]
).dt.total_seconds() / 60

# 完整性与未来信息检查。
matched = merged["port_status_time_utc"].notna()
future_status = (
    merged.loc[matched, "port_status_time_utc"]
    > merged.loc[matched, "sample_time_utc"]
).sum()

print("\n=== Port Status 匹配审计 ===")
print("成功匹配数：", matched.sum(), "/", len(merged))
print(f"匹配覆盖率：{matched.mean() * 100:.2f}%")
print("未来状态匹配违规数：", future_status)

if future_status > 0:
    raise ValueError("发现 Port Status 时间晚于 sample_time，停止。")

if matched.any():
    print("\nPort Status 滞后分钟：")
    print(
        merged.loc[matched, "port_status_lag_minutes"]
        .describe()[["min", "50%", "max"]]
    )

print("\n港口状态特征缺失数：")
print(merged[status_features].isna().sum())

merged.to_parquet(OUTPUT_FILE, index=False)

print("\n最终样本数：", len(merged))
print("已保存：", OUTPUT_FILE.name)
