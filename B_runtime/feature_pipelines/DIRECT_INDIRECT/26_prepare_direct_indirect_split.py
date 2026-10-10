from pathlib import Path
from pipeline_paths import resolve_data_dir
import json
import numpy as np
import pandas as pd

from sklearn.model_selection import train_test_split

DATA_DIR = resolve_data_dir(Path(__file__).resolve().parent / "项目")

INPUT_FILE = DATA_DIR / "25e_direct_indirect_features_final.parquet"
OUTPUT_FILE = DATA_DIR / "26_direct_indirect_features_with_split.parquet"
MANIFEST_FILE = DATA_DIR / "26_direct_indirect_feature_manifest.json"

RANDOM_SEED = 20260929

data = pd.read_parquet(INPUT_FILE)

target_column = "direct_indirect_binary"
group_column = "port_call_id"

if data[group_column].duplicated().any():
    raise ValueError("一个 Port Call 只能对应一条分类 snapshot。")

if not data["label_confidence"].eq("high").all():
    raise ValueError("主分类模型只能使用 high 标签。")

# 清洗 AIS 角度字段
for column in ["cog_deg", "heading_deg"]:
    data.loc[
        ~data[column].between(0, 360, inclusive="left"),
        column,
    ] = np.nan

# is_in_external_approach 全部为 0，无法提供区分信息，因此排除。
numeric_features = [
    "lon",
    "lat",
    "sog_knots",
    "cog_deg",
    "heading_deg",

    "distance_to_gate_km",
    "bearing_to_gate_deg",
    "heading_gate_difference_deg",
    "gate_side_binary",

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

    "cog_change_30min_deg",
    "cog_stability_30min",
    "heading_stability_30min",
    "trajectory_displacement_30min_km",
    "trajectory_distance_30min_km",
]

categorical_features = ["nav_status"]

required = (
    numeric_features
    + categorical_features
    + [target_column, group_column]
)

missing = set(required) - set(data.columns)
if missing:
    raise ValueError(f"缺少字段：{missing}")

# ---------- 分层 70 / 15 / 15 ----------
indices = np.arange(len(data))

train_idx, holdout_idx = train_test_split(
    indices,
    train_size=0.70,
    random_state=RANDOM_SEED,
    stratify=data[target_column],
)

validation_idx, test_idx = train_test_split(
    holdout_idx,
    test_size=0.50,
    random_state=RANDOM_SEED,
    stratify=data.iloc[holdout_idx][target_column],
)

data["dataset_split"] = "unassigned"
data.loc[train_idx, "dataset_split"] = "train"
data.loc[validation_idx, "dataset_split"] = "validation"
data.loc[test_idx, "dataset_split"] = "test"

if data["dataset_split"].eq("unassigned").any():
    raise ValueError("存在未分配样本。")

print("=== Direct/Indirect 分层划分 ===")
print(
    pd.crosstab(
        data["dataset_split"],
        data[target_column],
        margins=True,
    )
)

print("\n=== 各集合样本数与 Indirect 比例 ===")
summary = data.groupby("dataset_split")[target_column].agg(
    samples="count",
    indirect_count="sum",
    indirect_rate="mean",
)

summary["indirect_rate"] = summary["indirect_rate"] * 100
print(summary.round(2))

print("\n=== 特征缺失概览 ===")
print(
    data[numeric_features + categorical_features]
    .isna()
    .sum()
    .sort_values(ascending=False)
    .head(15)
)

data.to_parquet(OUTPUT_FILE, index=False)

manifest = {
    "task": "Direct / Indirect classification at ARR minus 4 hours",
    "label": {
        "column": target_column,
        "direct": 0,
        "indirect": 1,
        "confidence_level": "high only",
    },
    "snapshot_policy": {
        "target_time": "ARR minus 4 hours",
        "max_ais_lookback_minutes": 30,
        "strict_rule": "sample_time_utc < arr_time_utc",
    },
    "split_policy": {
        "unit": "port_call_id",
        "strategy": "stratified 70/15/15",
        "random_seed": RANDOM_SEED,
    },
    "numeric_features": numeric_features,
    "categorical_features": categorical_features,
    "excluded_from_model": [
        "port_call_id",
        "arr_event_id",
        "mmsi",
        "sample_time_utc",
        "target_time_utc",
        "arr_time_utc",
        "snapshot_lag_minutes",
        "direct_indirect_binary",
        "label_confidence",
        "gate_side",
        "is_in_external_approach",
        "trajectory_distance_30min_km_raw",
        "trajectory_jump_segment_count_30min",
    ],
}

with MANIFEST_FILE.open("w", encoding="utf-8") as f:
    json.dump(manifest, f, ensure_ascii=False, indent=2)

print(f"\n已保存分类数据：{OUTPUT_FILE.name}")
print(f"已保存特征清单：{MANIFEST_FILE.name}")
