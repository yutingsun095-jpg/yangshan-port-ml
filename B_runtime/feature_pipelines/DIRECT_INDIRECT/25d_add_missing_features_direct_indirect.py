from pathlib import Path
from pipeline_paths import resolve_data_dir
import json
import numpy as np
import pandas as pd
import duckdb
from shapely.geometry import Point, shape

DATA_DIR = resolve_data_dir(Path(__file__).resolve().parent / "项目")

AIS_FILE = DATA_DIR / "stage2_yangshan_202401.parquet"
INPUT_FILE = DATA_DIR / "25c_direct_indirect_dynamic_features.parquet"
GEO_FILE = DATA_DIR / "yangshan_geofence_candidate_v2确定版.geojson"
OUTPUT_FILE = DATA_DIR / "25d_direct_indirect_features_complete.parquet"

# ---------- 1. 读取已有 VAT snapshot 特征 ----------
features = pd.read_parquet(INPUT_FILE)
features["sample_time_utc"] = pd.to_datetime(
    features["sample_time_utc"], utc=True
)

# ---------- 2. 从真实 GeoJSON 构造 External Approach 特征 ----------
with GEO_FILE.open("r", encoding="utf-8") as f:
    geojson = json.load(f)

external_matches = [
    feature
    for feature in geojson["features"]
    if feature.get("properties", {}).get("name")
    == "Yangshan_External_Approach_Area"
]

if len(external_matches) != 1:
    raise ValueError(
        "未找到唯一的 Yangshan_External_Approach_Area。"
    )

external_area = shape(external_matches[0]["geometry"])

# covers() 包含边界上的点
features["is_in_external_approach"] = [
    int(external_area.covers(Point(lon, lat)))
    for lon, lat in zip(features["lon"], features["lat"])
]

# ---------- 3. 严格回看 sample_time 之前 30 分钟的 AIS ----------
con = duckdb.connect()
con.execute("PRAGMA enable_progress_bar")
con.register("snapshots", features)

query = """
WITH valid_ais AS (
    SELECT
        CAST(mmsi AS VARCHAR) AS mmsi,
        timestamp_utc,
        lon,
        lat,
        heading_deg
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
        a.heading_deg
    FROM snapshots AS s
    INNER JOIN valid_ais AS a
        ON s.mmsi = a.mmsi
        -- 严格仅使用 sample_time 之前的数据
        AND a.timestamp_utc < s.sample_time_utc
        AND a.timestamp_utc >= s.sample_time_utc - INTERVAL 30 MINUTE
),
ordered_history AS (
    SELECT
        *,
        LAG(lon) OVER (
            PARTITION BY arr_event_id, sample_time_utc
            ORDER BY timestamp_utc
        ) AS previous_lon,
        LAG(lat) OVER (
            PARTITION BY arr_event_id, sample_time_utc
            ORDER BY timestamp_utc
        ) AS previous_lat
    FROM history
)
SELECT
    arr_event_id,
    sample_time_utc,

    COUNT(*) AS history_point_count_30min,

    -- 历史航向稳定度：越接近 1，说明航向越稳定
    SQRT(
        POWER(
            AVG(
                SIN(
                    RADIANS(
                        CASE
                            WHEN heading_deg >= 0 AND heading_deg < 360
                            THEN heading_deg
                        END
                    )
                )
            ),
            2
        )
        +
        POWER(
            AVG(
                COS(
                    RADIANS(
                        CASE
                            WHEN heading_deg >= 0 AND heading_deg < 360
                            THEN heading_deg
                        END
                    )
                )
            ),
            2
        )
    ) AS heading_stability_30min,

    -- 过去 30 分钟连续 AIS 点之间的累计航迹距离，单位 km
    SUM(
        CASE
            WHEN previous_lon IS NOT NULL AND previous_lat IS NOT NULL
            THEN
                2 * 6371.0088 * ASIN(
                    SQRT(
                        POWER(
                            SIN(RADIANS(lat - previous_lat) / 2),
                            2
                        )
                        +
                        COS(RADIANS(previous_lat))
                        * COS(RADIANS(lat))
                        * POWER(
                            SIN(RADIANS(lon - previous_lon) / 2),
                            2
                        )
                    )
                )
        END
    ) AS trajectory_distance_30min_km

FROM ordered_history
GROUP BY
    arr_event_id,
    sample_time_utc
"""

additional = con.execute(query, [str(AIS_FILE)]).df()
con.close()

additional["sample_time_utc"] = pd.to_datetime(
    additional["sample_time_utc"], utc=True
)

features = features.merge(
    additional,
    on=["arr_event_id", "sample_time_utc"],
    how="left",
    validate="one_to_one",
)

# ---------- 4. 审计与保存 ----------
print("=== External Approach 特征 ===")
print(features["is_in_external_approach"].value_counts().sort_index())
print(
    "区域内比例：",
    f"{features['is_in_external_approach'].mean() * 100:.2f}%"
)

print("\n=== 新增动态特征缺失数 ===")
for column in [
    "heading_stability_30min",
    "trajectory_distance_30min_km",
]:
    print(f"{column}: {int(features[column].isna().sum())}")

print("\n=== 30 分钟累计航迹距离（km） ===")
print(
    features["trajectory_distance_30min_km"]
    .describe()[["min", "50%", "max"]]
)

features.to_parquet(OUTPUT_FILE, index=False)

print(f"\n最终 VAT baseline 样本数：{len(features)}")
print(f"已保存：{OUTPUT_FILE.name}")
