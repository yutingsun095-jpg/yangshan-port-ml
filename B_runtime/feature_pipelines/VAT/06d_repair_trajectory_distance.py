from pathlib import Path
from pipeline_paths import resolve_data_dir
import pandas as pd
import duckdb

DATA_DIR = resolve_data_dir(Path(__file__).resolve().parent)

AIS_FILE = DATA_DIR / "stage2_yangshan_202401.parquet"
INPUT_FILE = DATA_DIR / "06b_high_snapshots_features_complete.parquet"
OUTPUT_FILE = DATA_DIR / "06d_high_snapshots_features_final.parquet"

MAX_PLAUSIBLE_SEGMENT_SPEED_KNOTS = 40.0
KM_PER_NAUTICAL_MILE = 1.852

features = pd.read_parquet(INPUT_FILE)
features["sample_time_utc"] = pd.to_datetime(
    features["sample_time_utc"], utc=True
)

con = duckdb.connect()
con.execute("PRAGMA enable_progress_bar")
con.register("snapshots", features)

query = """
WITH valid_ais AS (
    SELECT
        CAST(mmsi AS VARCHAR) AS mmsi,
        timestamp_utc,
        lon,
        lat
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
        a.lat
    FROM snapshots AS s
    INNER JOIN valid_ais AS a
        ON s.mmsi = a.mmsi
        AND a.timestamp_utc < s.sample_time_utc
        AND a.timestamp_utc >= s.sample_time_utc - INTERVAL 30 MINUTE
),
ordered_history AS (
    SELECT
        *,
        LAG(timestamp_utc) OVER (
            PARTITION BY arr_event_id, sample_time_utc
            ORDER BY timestamp_utc
        ) AS previous_time_utc,
        LAG(lon) OVER (
            PARTITION BY arr_event_id, sample_time_utc
            ORDER BY timestamp_utc
        ) AS previous_lon,
        LAG(lat) OVER (
            PARTITION BY arr_event_id, sample_time_utc
            ORDER BY timestamp_utc
        ) AS previous_lat
    FROM history
),
segments AS (
    SELECT
        *,
        DATE_DIFF(
            'millisecond',
            previous_time_utc,
            timestamp_utc
        ) / 3600000.0 AS segment_hours,

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
        END AS segment_distance_km
    FROM ordered_history
),
segment_speed AS (
    SELECT
        *,
        CASE
            WHEN segment_hours > 0
            THEN segment_distance_km
                 / segment_hours
                 / 1.852
        END AS implied_segment_speed_knots
    FROM segments
)
SELECT
    arr_event_id,
    sample_time_utc,

    SUM(
        CASE
            WHEN implied_segment_speed_knots
                 <= ?
            THEN segment_distance_km
        END
    ) AS trajectory_distance_30min_km_robust,

    SUM(
        CASE
            WHEN implied_segment_speed_knots
                 > ?
            THEN 1
            ELSE 0
        END
    ) AS trajectory_jump_segment_count_30min

FROM segment_speed
GROUP BY
    arr_event_id,
    sample_time_utc
"""

robust_distance = con.execute(
    query,
    [
        str(AIS_FILE),
        MAX_PLAUSIBLE_SEGMENT_SPEED_KNOTS,
        MAX_PLAUSIBLE_SEGMENT_SPEED_KNOTS,
    ],
).df()

con.close()

robust_distance["sample_time_utc"] = pd.to_datetime(
    robust_distance["sample_time_utc"],
    utc=True,
)

# 保留原始距离仅供审计，不作为模型特征。
features = features.rename(
    columns={
        "trajectory_distance_30min_km":
        "trajectory_distance_30min_km_raw"
    }
)

features = features.merge(
    robust_distance,
    on=["arr_event_id", "sample_time_utc"],
    how="left",
    validate="one_to_one",
)

# 最终模型特征使用物理合理的累计航迹距离
features["trajectory_distance_30min_km"] = (
    features["trajectory_distance_30min_km_robust"]
)

features = features.drop(
    columns=["trajectory_distance_30min_km_robust"]
)

print("=== 跳点处理规则 ===")
print(
    "仅累计隐含线段速度不超过",
    f"{MAX_PLAUSIBLE_SEGMENT_SPEED_KNOTS:.0f} knots",
    "的 AIS 相邻线段。"
)

print("\n=== 疑似跳点线段 ===")
affected = int(
    (features["trajectory_jump_segment_count_30min"] > 0).sum()
)
print("受跳点影响的 snapshot 数：", affected)
print(
    "占比：",
    f"{affected / len(features) * 100:.2f}%"
)

print("\n=== 修正后累计航迹距离（km） ===")
print(
    features["trajectory_distance_30min_km"]
    .describe()[["min", "50%", "max"]]
)

print("\n=== 修正前后最大值（km） ===")
print(
    "原始最大值：",
    features["trajectory_distance_30min_km_raw"].max()
)
print(
    "修正后最大值：",
    features["trajectory_distance_30min_km"].max()
)

features.to_parquet(OUTPUT_FILE, index=False)

print(f"\n最终 VAT baseline 样本数：{len(features)}")
print(f"已保存：{OUTPUT_FILE.name}")
