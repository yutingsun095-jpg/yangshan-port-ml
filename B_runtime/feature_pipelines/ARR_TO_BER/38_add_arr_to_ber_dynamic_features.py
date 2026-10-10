from pathlib import Path
from pipeline_paths import resolve_data_dir
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds

DATA_DIR = resolve_data_dir(Path(__file__).resolve().parent / "项目")

INPUT_FILE = DATA_DIR / "37_arr_to_ber_snapshots_spatial.parquet"
AIS_FILE = DATA_DIR / "stage2_yangshan_202401.parquet"
OUTPUT_FILE = DATA_DIR / "38_arr_to_ber_features_dynamic.parquet"

TIME_DTYPE = "datetime64[ns, UTC]"
WINDOWS_MINUTES = [10, 20, 30]
MAX_IMPLIED_SEGMENT_SPEED_KNOTS = 40.0


def angular_difference_deg(a, b):
    return np.abs((a - b + 180) % 360 - 180)


def haversine_km(lon1, lat1, lon2, lat2):
    radius_km = 6371.0088

    lon1 = np.radians(lon1)
    lat1 = np.radians(lat1)
    lon2 = np.radians(lon2)
    lat2 = np.radians(lat2)

    dlon = lon2 - lon1
    dlat = lat2 - lat1

    a = (
        np.sin(dlat / 2) ** 2
        + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    )
    return 2 * radius_km * np.arcsin(np.sqrt(a))


def slope_per_minute(times, values):
    """以分钟为横轴的线性趋势；少于两个有效点时返回 NaN。"""
    if len(values) < 2:
        return np.nan

    x = (times - times.iloc[0]).dt.total_seconds().to_numpy() / 60
    y = values.to_numpy(dtype=float)

    if np.unique(x).size < 2:
        return np.nan

    return np.polyfit(x, y, deg=1)[0]


def robust_trajectory_distance_km(history):
    """
    只累计隐含速度 <= 40 knots 的相邻 AIS 线段。
    返回：累计距离、被剔除的疑似跳点线段数。
    """
    positions = history.dropna(subset=["lon", "lat"]).sort_values("timestamp_utc")

    if len(positions) < 2:
        return np.nan, 0

    total_km = 0.0
    skipped_segments = 0

    previous = positions.iloc[0]

    for _, current in positions.iloc[1:].iterrows():
        dt_hours = (
            current["timestamp_utc"] - previous["timestamp_utc"]
        ).total_seconds() / 3600

        if dt_hours <= 0:
            previous = current
            continue

        segment_km = haversine_km(
            previous["lon"], previous["lat"],
            current["lon"], current["lat"],
        )

        implied_knots = segment_km / dt_hours / 1.852

        if implied_knots <= MAX_IMPLIED_SEGMENT_SPEED_KNOTS:
            total_km += segment_km
        else:
            skipped_segments += 1

        previous = current

    return total_km, skipped_segments


# 1. 读取 ARR 前 snapshot，并再次验证时间边界。
snapshots = pd.read_parquet(INPUT_FILE)
snapshots["sample_time_utc"] = pd.to_datetime(
    snapshots["sample_time_utc"], utc=True
).astype(TIME_DTYPE)

if (snapshots["sample_time_utc"] >= snapshots["arr_time_utc"]).any():
    raise ValueError("发现 sample_time_utc >= arr_time_utc，停止。")

print("=== Snapshot 基本信息 ===")
print("样本数：", len(snapshots))
print("Port Call 数：", snapshots["port_call_id"].nunique())

# 2. 仅读取目标 MMSI、每个 sample_time 前最多 30 分钟范围所需的 AIS。
scan_start = snapshots["sample_time_utc"].min() - pd.Timedelta(minutes=30)
scan_end = snapshots["sample_time_utc"].max()

ais_dataset = ds.dataset(AIS_FILE, format="parquet")

ais_filter = (
    ds.field("mmsi").isin(snapshots["mmsi"].astype(str).unique().tolist())
    & (ds.field("timestamp_utc") >= pa.scalar(scan_start.to_pydatetime()))
    & (ds.field("timestamp_utc") <= pa.scalar(scan_end.to_pydatetime()))
)

ais = (
    ais_dataset.to_table(
        columns=[
            "mmsi", "timestamp_utc", "lon", "lat",
            "sog_knots", "cog_deg", "heading_deg",
        ],
        filter=ais_filter,
    )
    .to_pandas()
)

ais["mmsi"] = ais["mmsi"].astype(str)
ais["timestamp_utc"] = pd.to_datetime(
    ais["timestamp_utc"], utc=True
).astype(TIME_DTYPE)

print("读取动态窗口 AIS 行数：", len(ais))
print("读取 AIS MMSI 数：", ais["mmsi"].nunique())

ais_by_mmsi = {
    mmsi: group.sort_values("timestamp_utc").reset_index(drop=True)
    for mmsi, group in ais.groupby("mmsi", sort=False)
}

# 3. 每个 snapshot 仅使用 timestamp_utc <= sample_time_utc 的历史 AIS。
rows = []

for i, snapshot in snapshots.iterrows():
    sample_time = snapshot["sample_time_utc"]
    vessel_ais = ais_by_mmsi.get(str(snapshot["mmsi"]))

    if vessel_ais is None:
        raise ValueError(f"未找到 MMSI={snapshot['mmsi']} 的历史 AIS。")

    result = {}

    for window in WINDOWS_MINUTES:
        start_time = sample_time - pd.Timedelta(minutes=window)

        history = vessel_ais.loc[
            (vessel_ais["timestamp_utc"] > start_time)
            & (vessel_ais["timestamp_utc"] <= sample_time)
        ].copy()

        result[f"history_point_count_{window}min"] = len(history)

        sog = history["sog_knots"].dropna()
        result[f"sog_mean_{window}min"] = sog.mean()
        result[f"sog_min_{window}min"] = sog.min()
        result[f"sog_max_{window}min"] = sog.max()

        if len(sog) >= 2:
            result[f"sog_change_{window}min"] = sog.iloc[-1] - sog.iloc[0]
        else:
            result[f"sog_change_{window}min"] = np.nan

        # 30 分钟窗口附加更丰富的动态特征。
        if window == 30:
            result["sog_std_30min"] = sog.std()

            valid_sog_history = history.dropna(subset=["sog_knots"])
            result["sog_acceleration_trend_30min"] = slope_per_minute(
                valid_sog_history["timestamp_utc"],
                valid_sog_history["sog_knots"],
            )

            cog_history = history.loc[
                history["cog_deg"].between(0, 359.999)
            ].copy()

            if len(cog_history) >= 2:
                cog_values = cog_history["cog_deg"].to_numpy()
                result["cog_change_30min_deg"] = angular_difference_deg(
                    cog_values[-1], cog_values[0]
                )

                cog_radians = np.radians(cog_values)
                result["cog_stability_30min"] = np.sqrt(
                    np.mean(np.cos(cog_radians)) ** 2
                    + np.mean(np.sin(cog_radians)) ** 2
                )
            else:
                result["cog_change_30min_deg"] = np.nan
                result["cog_stability_30min"] = np.nan

            heading_history = history.loc[
                history["heading_deg"].between(0, 359.999)
            ].copy()

            if len(heading_history) >= 2:
                heading_radians = np.radians(
                    heading_history["heading_deg"].to_numpy()
                )
                result["heading_stability_30min"] = np.sqrt(
                    np.mean(np.cos(heading_radians)) ** 2
                    + np.mean(np.sin(heading_radians)) ** 2
                )
            else:
                result["heading_stability_30min"] = np.nan

            position_history = history.dropna(subset=["lon", "lat"])
            if len(position_history) >= 2:
                first = position_history.iloc[0]
                last = position_history.iloc[-1]

                result["trajectory_displacement_30min_km"] = haversine_km(
                    first["lon"], first["lat"], last["lon"], last["lat"]
                )
            else:
                result["trajectory_displacement_30min_km"] = np.nan

            trajectory_km, skipped = robust_trajectory_distance_km(history)
            result["trajectory_distance_30min_km"] = trajectory_km
            result["trajectory_skipped_jump_segments_30min"] = skipped

    rows.append(result)

    if (i + 1) % 50 == 0 or i + 1 == len(snapshots):
        print(f"已完成动态特征：{i + 1} / {len(snapshots)}")

dynamic_features = pd.DataFrame(rows)
features = pd.concat(
    [snapshots.reset_index(drop=True), dynamic_features],
    axis=1,
)

# 4. 完整性审计。
dynamic_cols = dynamic_features.columns.tolist()

print("\n=== 动态特征缺失数 ===")
print(features[dynamic_cols].isna().sum().sort_values(ascending=False))

print("\n=== 历史 AIS 点数 ===")
history_cols = [
    "history_point_count_10min",
    "history_point_count_20min",
    "history_point_count_30min",
]
print(features[history_cols].describe().loc[["min", "50%", "max"]])

print("\n=== 30 分钟累计轨迹距离（km） ===")
print(
    features["trajectory_distance_30min_km"]
    .describe()[["min", "50%", "max"]]
)

print("\n=== 疑似跳点处理 ===")
affected = (features["trajectory_skipped_jump_segments_30min"] > 0).sum()
print("受影响 snapshot 数：", affected)
print(f"占比：{affected / len(features) * 100:.2f}%")
print(f"规则：仅累计隐含速度 <= {MAX_IMPLIED_SEGMENT_SPEED_KNOTS} knots 的线段。")

features.to_parquet(OUTPUT_FILE, index=False)

print("\n最终样本数：", len(features))
print("已保存：", OUTPUT_FILE.name)
