from pathlib import Path
from pipeline_paths import resolve_data_dir
import json
import numpy as np
import pandas as pd

DATA_DIR = resolve_data_dir(Path(__file__).resolve().parent)

INPUT_FILE = DATA_DIR / "06d_high_snapshots_features_final.parquet"
OUTPUT_FILE = DATA_DIR / "07_high_features_with_split.parquet"
MANIFEST_FILE = DATA_DIR / "07_feature_manifest.json"

RANDOM_SEED = 20260917

data = pd.read_parquet(INPUT_FILE)

# ---------- 1. 安全检查与基础清洗 ----------
if not data["confidence_level"].eq("high").all():
    raise ValueError("主模型数据中出现了非 high ARR。")

if (data["remaining_time_to_arr_min"] <= 0).any():
    raise ValueError("发现非正 VAT 标签，请检查 snapshot 构造。")

for column in ["cog_deg", "heading_deg"]:
    invalid = ~data[column].between(0, 360, inclusive="left")
    print(f"{column} 无效或缺失数：{int(invalid.sum())}")
    data.loc[invalid, column] = np.nan

# ---------- 2. VAT baseline 特征清单 ----------
numeric_features = [
    # 当前 AIS 状态
    "lon",
    "lat",
    "sog_knots",
    "cog_deg",
    "heading_deg",

    # Arrival Gate 与 External Approach 空间特征
    "distance_to_gate_km",
    "bearing_to_gate_deg",
    "heading_gate_difference_deg",
    "gate_side_binary",
    "is_in_external_approach",

    # 历史 SOG 特征
    "sog_mean_10min",
    "sog_mean_20min",
    "sog_mean_30min",
    "sog_min_10min",
    "sog_min_20min",
    "sog_min_30min",
    "sog_max_10min",
    "sog_max_20min",
    "sog_max_30min",
    "sog_std_30min",
    "sog_change_10min",
    "sog_change_30min",
    "sog_acceleration_trend_30min",

    # 历史 COG / Heading / 轨迹特征
    "cog_change_30min_deg",
    "cog_stability_30min",
    "heading_stability_30min",
    "trajectory_displacement_30min_km",
    "trajectory_distance_30min_km",
]

categorical_features = [
    "nav_status",
]

target_column = "remaining_time_to_arr_min"
group_column = "arr_event_id"

required_columns = (
    numeric_features
    + categorical_features
    + [target_column, group_column]
)

missing_columns = set(required_columns) - set(data.columns)
if missing_columns:
    raise ValueError(f"缺少字段：{missing_columns}")

# ---------- 3. 以 ARR 事件为唯一分组单位的 70/15/15 划分 ----------
event_ids = np.array(sorted(data[group_column].unique()))

rng = np.random.default_rng(RANDOM_SEED)
rng.shuffle(event_ids)

n_events = len(event_ids)
n_train = round(n_events * 0.70)   # 454
n_valid = round(n_events * 0.15)   # 97

train_events = set(event_ids[:n_train])
valid_events = set(event_ids[n_train:n_train + n_valid])
test_events = set(event_ids[n_train + n_valid:])

data["dataset_split"] = np.select(
    [
        data[group_column].isin(train_events),
        data[group_column].isin(valid_events),
        data[group_column].isin(test_events),
    ],
    ["train", "validation", "test"],
    default="unassigned",
)

if data["dataset_split"].eq("unassigned").any():
    raise ValueError("存在未分配的 ARR 事件。")

# 同一 ARR 的全部 snapshot 必须只能在一个集合中
split_count = data.groupby(group_column)["dataset_split"].nunique()
if split_count.max() != 1:
    raise ValueError("错误：同一个 ARR 被分配到多个集合。")

# ---------- 4. 审计输出 ----------
print("\n=== ARR 事件级划分 ===")
for split in ["train", "validation", "test"]:
    subset = data.loc[data["dataset_split"].eq(split)]

    print(
        f"{split:>10}: "
        f"{subset[group_column].nunique():>3} 个 ARR 事件，"
        f"{len(subset):>5} 条 snapshot"
    )

print("\n=== VAT 分布（分钟） ===")
print(
    data.groupby("dataset_split")[target_column]
    .describe()[["count", "mean", "50%", "min", "max"]]
)

print("\n=== 模型特征缺失概览 ===")
print(
    data[numeric_features + categorical_features]
    .isna()
    .sum()
    .sort_values(ascending=False)
    .head(15)
)

print("\n=== nav_status 类别 ===")
print(data["nav_status"].value_counts(dropna=False).head(20))

# ---------- 5. 保存数据与特征说明 ----------
data.to_parquet(OUTPUT_FILE, index=False)

manifest = {
    "task": "VAT baseline: AIS + GeoJSON",
    "ground_truth": {
        "target": target_column,
        "definition": (
            "ARR event_time_utc minus actual sample_time_utc, in minutes"
        ),
        "confidence_level": "high only",
    },
    "snapshot_policy": {
        "main_window_hours": 6,
        "snapshot_interval_minutes": 10,
        "max_lookback_minutes": 30,
        "strict_rule": "sample_time_utc < arr_time_utc",
    },
    "trajectory_qc": {
        "model_feature": "trajectory_distance_30min_km",
        "rule": (
            "Exclude AIS adjacent segments whose implied speed exceeds "
            "40 knots when summing trajectory distance."
        ),
        "excluded_audit_columns": [
            "trajectory_distance_30min_km_raw",
            "trajectory_jump_segment_count_30min",
        ],
    },
    "split_policy": {
        "unit": "arr_event_id",
        "random_seed": RANDOM_SEED,
        "events": {
            "train": len(train_events),
            "validation": len(valid_events),
            "test": len(test_events),
        },
        "rule": (
            "All snapshots from the same ARR event must belong "
            "to exactly one dataset split."
        ),
    },
    "numeric_features": numeric_features,
    "categorical_features": categorical_features,
    "excluded_from_model": [
        "arr_event_id",
        "mmsi",
        "sample_time_utc",
        "arr_time_utc",
        "target_snapshot_time_utc",
        "planned_lead_minutes",
        "snapshot_lag_minutes",
        "remaining_time_to_arr_min",
        "trajectory_distance_30min_km_raw",
        "trajectory_jump_segment_count_30min",
    ],
}

with MANIFEST_FILE.open("w", encoding="utf-8") as f:
    json.dump(manifest, f, ensure_ascii=False, indent=2)

print(f"\n已保存模型数据：{OUTPUT_FILE.name}")
print(f"已保存特征清单：{MANIFEST_FILE.name}")
