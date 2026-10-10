from pathlib import Path
from pipeline_paths import resolve_data_dir
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds

DATA_DIR = resolve_data_dir(Path(__file__).resolve().parent / "项目")

AIS_FILE = DATA_DIR / "stage2_yangshan_202401.parquet"
PORT_CALL_FILE = DATA_DIR / "stage4_yangshan_port_calls_final_202401确定版.parquet"
OUTPUT_FILE = DATA_DIR / "36_arr_to_ber_snapshots_raw.parquet"

TIME_DTYPE = "datetime64[ns, UTC]"
MAX_AIS_LAG_MINUTES = 5

# 1. 仅保留主 ground truth：ARR=high、BER=high。
port_calls = pd.read_parquet(PORT_CALL_FILE)

required_cols = [
    "port_call_id", "mmsi", "arr_event_id", "arr_time_utc",
    "ber_event_id", "ber_time_utc", "arr_to_ber_min",
    "arr_confidence_level", "ber_confidence_level", "has_ber",
]
missing = [c for c in required_cols if c not in port_calls.columns]
if missing:
    raise KeyError(f"F7 缺少字段：{missing}")

events = port_calls.loc[
    (port_calls["has_ber"] == True)
    & (port_calls["arr_confidence_level"] == "high")
    & (port_calls["ber_confidence_level"] == "high")
    & port_calls["arr_to_ber_min"].notna()
    & (port_calls["arr_to_ber_min"] > 0),
    required_cols,
].copy()

events["mmsi"] = events["mmsi"].astype(str)
events["arr_time_utc"] = pd.to_datetime(
    events["arr_time_utc"], utc=True
).astype(TIME_DTYPE)
events["ber_time_utc"] = pd.to_datetime(
    events["ber_time_utc"], utc=True
).astype(TIME_DTYPE)

print("=== ARR→BER 主任务 ===")
print("Port Call 数：", len(events))
print("MMSI 数：", events["mmsi"].nunique())
print("最大 AIS 允许滞后（分钟）：", MAX_AIS_LAG_MINUTES)

# 2. 仅读取目标船舶、ARR 前 5 分钟窗口内的 AIS。
scan_start = events["arr_time_utc"].min() - pd.Timedelta(
    minutes=MAX_AIS_LAG_MINUTES
)
scan_end = events["arr_time_utc"].max()

ais_dataset = ds.dataset(AIS_FILE, format="parquet")

ais_filter = (
    ds.field("mmsi").isin(events["mmsi"].unique().tolist())
    & (ds.field("timestamp_utc") >= pa.scalar(scan_start.to_pydatetime()))
    & (ds.field("timestamp_utc") < pa.scalar(scan_end.to_pydatetime()))
)

ais = (
    ais_dataset.to_table(
        columns=[
            "mmsi", "timestamp_utc", "lon", "lat",
            "sog_knots", "cog_deg", "heading_deg", "nav_status",
        ],
        filter=ais_filter,
    )
    .to_pandas()
)

ais["mmsi"] = ais["mmsi"].astype(str)
ais["timestamp_utc"] = pd.to_datetime(
    ais["timestamp_utc"], utc=True
).astype(TIME_DTYPE)

print("\n读取用于即时 snapshot 的 AIS 行数：", len(ais))

# 3. 严格选择 ARR 之前的最后一条 AIS。
events_sorted = events.sort_values("arr_time_utc").reset_index(drop=True)
ais_sorted = ais.sort_values("timestamp_utc").reset_index(drop=True)

snapshots = pd.merge_asof(
    events_sorted,
    ais_sorted,
    left_on="arr_time_utc",
    right_on="timestamp_utc",
    by="mmsi",
    direction="backward",
    allow_exact_matches=False,  # 严格要求 AIS 时间 < ARR 时间
    tolerance=pd.Timedelta(minutes=MAX_AIS_LAG_MINUTES),
)

snapshots = snapshots.rename(columns={"timestamp_utc": "sample_time_utc"})
snapshots["snapshot_lag_minutes"] = (
    snapshots["arr_time_utc"] - snapshots["sample_time_utc"]
).dt.total_seconds() / 60

# 4. 严格完整性检查。
missing_snapshot = snapshots["sample_time_utc"].isna().sum()
future_or_equal = (
    snapshots["sample_time_utc"] >= snapshots["arr_time_utc"]
).sum()

print("\n=== Snapshot 构造结果 ===")
print("目标 Port Call 数：", len(events))
print("成功匹配 snapshot 数：", len(snapshots) - missing_snapshot)
print("缺失 snapshot 数：", missing_snapshot)
print("sample_time >= ARR 的违规数：", future_or_equal)

if missing_snapshot > 0:
    raise ValueError("存在未匹配 AIS 的 Port Call；请停止后续建模并检查。")

if future_or_equal > 0:
    raise ValueError("发现使用 ARR 当刻或之后 AIS 的风险；请停止后续建模。")

print("\nAIS 滞后分钟：")
print(snapshots["snapshot_lag_minutes"].describe()[["min", "50%", "max"]])

print("\n目标 arr_to_ber_min 分布：")
print(snapshots["arr_to_ber_min"].describe()[["min", "25%", "50%", "75%", "max"]])

output_cols = [
    "port_call_id", "mmsi", "arr_event_id", "ber_event_id",
    "sample_time_utc", "arr_time_utc", "ber_time_utc",
    "snapshot_lag_minutes", "arr_to_ber_min",
    "arr_confidence_level", "ber_confidence_level",
    "lon", "lat", "sog_knots", "cog_deg", "heading_deg", "nav_status",
]

snapshots[output_cols].to_parquet(OUTPUT_FILE, index=False)

print("\n已保存：", OUTPUT_FILE.name)
print("\n前五行：")
print(snapshots[output_cols].head())
