from pathlib import Path
from pipeline_paths import resolve_data_dir
import numpy as np
import pandas as pd
import duckdb

DATA_DIR = resolve_data_dir(Path(__file__).resolve().parent / "项目")

AIS_FILE = DATA_DIR / "stage2_yangshan_202401.parquet"
SNAPSHOT_FILE = DATA_DIR / "25a_direct_indirect_gate_features.parquet"
OUTPUT_FILE = DATA_DIR / "25c_direct_indirect_dynamic_features.parquet"

EARTH_RADIUS_KM = 6371.0088

snapshots = pd.read_parquet(SNAPSHOT_FILE)

# 保证时区格式正确
snapshots["sample_time_utc"] = pd.to_datetime(
    snapshots["sample_time_utc"], utc=True
)

print("Snapshot 数：", len(snapshots))

# DuckDB 负责从 5,254 万行 AIS 中，只读取本次动态特征需要的记录。
con = duckdb.connect()
con.execute("PRAGMA enable_progress_bar")
con.execute("SET progress_bar_time = 1000")
con.register("snapshots", snapshots)

query = """
WITH valid_ais AS (
    SELECT
        CAST(mmsi AS VARCHAR) AS mmsi,
        timestamp_utc,
        lon,
        lat,
        sog_knots,
        cog_deg
    FROM read_parquet(?)
    WHERE
        COALESCE(qc_malformed, FALSE) = FALSE
        AND COALESCE(qc_invalid_position, FALSE) = FALSE
        AND COALESCE(qc_duplicate, FALSE) = FALSE
        AND timestamp_utc IS NOT NULL
        AND lon IS NOT NULL
        AND lat IS NOT NULL
),
history AS (
    SELECT
        s.arr_event_id,
        s.sample_time_utc,

        a.timestamp_utc,
        a.lon,
        a.lat,
        a.sog_knots,
        a.cog_deg

    FROM snapshots AS s
    LEFT JOIN valid_ais AS a
        ON s.mmsi = a.mmsi
        -- 严格小于 sample_time，不使用当前 snapshot 或未来 AIS。
        AND a.timestamp_utc < s.sample_time_utc
        -- 最长只回看 30 分钟。
        AND a.timestamp_utc >= s.sample_time_utc - INTERVAL 30 MINUTE
)
SELECT
    arr_event_id,
    sample_time_utc,

    COUNT(timestamp_utc) FILTER (
        WHERE timestamp_utc >= sample_time_utc - INTERVAL 10 MINUTE
    ) AS history_count_10min,

    COUNT(timestamp_utc) FILTER (
        WHERE timestamp_utc >= sample_time_utc - INTERVAL 20 MINUTE
    ) AS history_count_20min,

    COUNT(timestamp_utc) AS history_count_30min,

    AVG(sog_knots) FILTER (
        WHERE timestamp_utc >= sample_time_utc - INTERVAL 10 MINUTE
    ) AS sog_mean_10min,

    AVG(sog_knots) FILTER (
        WHERE timestamp_utc >= sample_time_utc - INTERVAL 20 MINUTE
    ) AS sog_mean_20min,

    AVG(sog_knots) AS sog_mean_30min,

    MIN(sog_knots) FILTER (
        WHERE timestamp_utc >= sample_time_utc - INTERVAL 10 MINUTE
    ) AS sog_min_10min,

    MIN(sog_knots) FILTER (
        WHERE timestamp_utc >= sample_time_utc - INTERVAL 20 MINUTE
    ) AS sog_min_20min,

    MIN(sog_knots) AS sog_min_30min,

    MAX(sog_knots) FILTER (
        WHERE timestamp_utc >= sample_time_utc - INTERVAL 10 MINUTE
    ) AS sog_max_10min,

    MAX(sog_knots) FILTER (
        WHERE timestamp_utc >= sample_time_utc - INTERVAL 20 MINUTE
    ) AS sog_max_20min,

    MAX(sog_knots) AS sog_max_30min,

AVG(sog_knots * sog_knots) AS sog_mean_square_30min,

    -- 30 分钟内最早 AIS，用于计算速度趋势与轨迹位移
    ARG_MIN(timestamp_utc, timestamp_utc) AS first_history_time_30min,
    ARG_MIN(sog_knots, timestamp_utc) AS first_sog_30min,
    ARG_MIN(lon, timestamp_utc) AS first_lon_30min,
    ARG_MIN(lat, timestamp_utc) AS first_lat_30min,

    -- COG 使用正弦/余弦均值，避免 359° 与 1° 被错误平均为 180°
    AVG(SIN(RADIANS(cog_deg))) AS cog_mean_sin_30min,
    AVG(COS(RADIANS(cog_deg))) AS cog_mean_cos_30min

FROM history
GROUP BY
    arr_event_id,
    sample_time_utc
"""

dynamic = con.execute(query, [str(AIS_FILE)]).df()
con.close()

dynamic["sample_time_utc"] = pd.to_datetime(
    dynamic["sample_time_utc"], utc=True
)
dynamic["first_history_time_30min"] = pd.to_datetime(
    dynamic["first_history_time_30min"], utc=True
)

# 合并动态特征
features = snapshots.merge(
    dynamic,
    on=["arr_event_id", "sample_time_utc"],
    how="left",
    validate="one_to_one",
)
features["sog_std_30min"] = np.sqrt(
    np.maximum(
        features["sog_mean_square_30min"]
        - features["sog_mean_30min"] ** 2,
        0,
    )
)

# 当前速度相对于过去窗口平均速度的变化
features["sog_change_10min"] = (
    features["sog_knots"] - features["sog_mean_10min"]
)
features["sog_change_30min"] = (
    features["sog_knots"] - features["sog_mean_30min"]
)

# 当前速度相对于过去最早记录的平均加速度趋势（knots/min）
elapsed_minutes = (
    features["sample_time_utc"]
    - features["first_history_time_30min"]
).dt.total_seconds() / 60

features["sog_acceleration_trend_30min"] = (
    (features["sog_knots"] - features["first_sog_30min"])
    / elapsed_minutes.replace(0, np.nan)
)

# 过去 30 分钟 COG 的圆形均值与当前 COG 的夹角
features["cog_mean_30min_deg"] = (
    np.degrees(
        np.arctan2(
            features["cog_mean_sin_30min"],
            features["cog_mean_cos_30min"],
        )
    )
    + 360
) % 360

features["cog_change_30min_deg"] = np.abs(
    (
        features["cog_deg"]
        - features["cog_mean_30min_deg"]
        + 180
    ) % 360
    - 180
)

# COG 稳定度：越接近 1，代表过去 30 分钟航向越稳定
features["cog_stability_30min"] = np.sqrt(
    features["cog_mean_sin_30min"] ** 2
    + features["cog_mean_cos_30min"] ** 2
)

# 过去 30 分钟的起点到当前位置位移（不是未来信息）
lat1 = np.radians(features["first_lat_30min"])
lon1 = np.radians(features["first_lon_30min"])
lat2 = np.radians(features["lat"])
lon2 = np.radians(features["lon"])

dlat = lat2 - lat1
dlon = lon2 - lon1

a = (
    np.sin(dlat / 2) ** 2
    + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
)

features["trajectory_displacement_30min_km"] = (
    2 * EARTH_RADIUS_KM * np.arctan2(np.sqrt(a), np.sqrt(1 - a))
)

# 删除仅为中间计算服务的列
features = features.drop(
    columns=[
        "first_history_time_30min",
        "first_sog_30min",
        "first_lon_30min",
        "first_lat_30min",
        "cog_mean_sin_30min",
        "cog_mean_cos_30min",
        "sog_mean_square_30min",
    ]
)

# ---------- 审计与保存 ----------
dynamic_feature_columns = [
    "sog_mean_10min",
    "sog_mean_20min",
    "sog_mean_30min",
    "sog_min_30min",
    "sog_max_30min",
    "sog_std_30min",
    "sog_change_30min",
    "sog_acceleration_trend_30min",
    "cog_change_30min_deg",
    "cog_stability_30min",
    "trajectory_displacement_30min_km",
]

print("\n=== 动态特征可用性 ===")
for column in dynamic_feature_columns:
    print(f"{column}: 缺失 {int(features[column].isna().sum())} 条")

print("\n=== 历史 AIS 数量 ===")
print(
    features[
        ["history_count_10min", "history_count_20min", "history_count_30min"]
    ].describe().loc[["min", "50%", "max"]]
)

features.to_parquet(OUTPUT_FILE, index=False)

print(f"\n最终特征样本数：{len(features)}")
print(f"已保存：{OUTPUT_FILE.name}")
