from pathlib import Path
from pipeline_paths import resolve_data_dir
import json
import pandas as pd
from sklearn.model_selection import train_test_split

DATA_DIR = resolve_data_dir(Path(__file__).resolve().parent / "项目")

INPUT_FILE = DATA_DIR / "39_arr_to_ber_features_with_port_status.parquet"
OUTPUT_FILE = DATA_DIR / "40_arr_to_ber_features_with_split.parquet"
MANIFEST_FILE = DATA_DIR / "40_arr_to_ber_feature_manifest.json"

RANDOM_STATE = 202401

df = pd.read_parquet(INPUT_FILE)

# 每行必须是一个唯一 Port Call，保证按 port_call_id 划分时不会泄漏。
print("=== Port Call 完整性 ===")
print("样本数：", len(df))
print("唯一 port_call_id 数：", df["port_call_id"].nunique())

if len(df) != df["port_call_id"].nunique():
    raise ValueError("一个 Port Call 不止一行，不能直接进行本脚本的事件级划分。")

if (df["sample_time_utc"] >= df["arr_time_utc"]).any():
    raise ValueError("发现 sample_time_utc >= arr_time_utc，停止。")

# 两个训练目标：主模型预测原始分钟；稳健性模型预测 log1p 后的分钟。
df["log1p_arr_to_ber_min"] = df["arr_to_ber_min"].map(
    lambda x: __import__("math").log1p(x)
)

# 不作为模型输入的字段：ID、时间、标签、后验 BER 信息、匹配辅助字段。
excluded_columns = [
    "port_call_id",
    "mmsi",
    "arr_event_id",
    "ber_event_id",
    "sample_time_utc",
    "arr_time_utc",
    "ber_time_utc",
    "arr_to_ber_min",
    "log1p_arr_to_ber_min",
    "arr_confidence_level",
    "ber_confidence_level",
    "port_status_time_utc",
    "snapshot_lag_minutes",
    "port_status_lag_minutes",
    "trajectory_skipped_jump_segments_30min",
    "is_in_main_berth_zone",
    "is_in_west_berth_zone",
]

candidate_features = [
    col for col in df.columns
    if col not in excluded_columns
]

# 恒定特征不具有预测信息；例如两个“是否在泊位区”字段应当被排除。
constant_features = [
    col for col in candidate_features
    if df[col].nunique(dropna=False) <= 1
]

model_features = [
    col for col in candidate_features
    if col not in constant_features
]

categorical_features = [
    col for col in model_features
    if col == "nav_status"
]

numeric_features = [
    col for col in model_features
    if col not in categorical_features
]

print("\n=== 特征清单 ===")
print("候选特征数：", len(candidate_features))
print("剔除恒定特征：", constant_features)
print("最终模型特征数：", len(model_features))
print("数值特征数：", len(numeric_features))
print("类别特征：", categorical_features)

print("\n=== 最终数值特征 ===")
for feature in numeric_features:
    print("-", feature)

# 目标的五分位分层，确保长等待样本不会集中在单一集合。
# 每个 Port Call 只有一行，因此该分层切分仍是严格 port_call_id 级划分。
df["target_stratum"] = pd.qcut(
    df["arr_to_ber_min"].rank(method="first"),
    q=5,
    labels=False,
)

train_df, temp_df = train_test_split(
    df,
    test_size=0.30,
    random_state=RANDOM_STATE,
    stratify=df["target_stratum"],
)

validation_df, test_df = train_test_split(
    temp_df,
    test_size=0.50,
    random_state=RANDOM_STATE,
    stratify=temp_df["target_stratum"],
)

train_df = train_df.copy()
validation_df = validation_df.copy()
test_df = test_df.copy()

train_df["dataset_split"] = "train"
validation_df["dataset_split"] = "validation"
test_df["dataset_split"] = "test"

model_df = pd.concat(
    [train_df, validation_df, test_df],
    ignore_index=True,
)

# 最终安全检查：同一 Port Call 只能在一个集合中。
split_counts = model_df.groupby("port_call_id")["dataset_split"].nunique()
cross_split_calls = (split_counts > 1).sum()

if cross_split_calls > 0:
    raise ValueError(f"发现 {cross_split_calls} 个 Port Call 跨集合泄漏。")

print("\n=== Port Call 级 70 / 15 / 15 划分 ===")
for name, subset in model_df.groupby("dataset_split"):
    print(f"{name:>10}: {len(subset):3d} 个 Port Call，"
          f"{subset['mmsi'].nunique():3d} 个 MMSI")

print("\n跨集合 Port Call 数：", cross_split_calls)

print("\n=== ARR→BER 标签分布（分钟） ===")
print(
    model_df.groupby("dataset_split")["arr_to_ber_min"]
    .describe()[["count", "mean", "50%", "min", "max"]]
)

print("\n=== 长等待样本在各集合的分布 ===")
long_wait_table = (
    model_df.assign(
        over_12h=model_df["arr_to_ber_min"] > 12 * 60,
        over_24h=model_df["arr_to_ber_min"] > 24 * 60,
    )
    .groupby("dataset_split")[["over_12h", "over_24h"]]
    .sum()
)
print(long_wait_table)

# target_stratum 仅用于分层，不允许进入模型。
model_df = model_df.drop(columns=["target_stratum"])

manifest = {
    "task": "ARR_to_BER_time_prediction",
    "unit_of_analysis": "one row per port_call_id",
    "ground_truth": {
        "arr_confidence_level": "high",
        "ber_confidence_level": "high",
        "target": "arr_to_ber_min",
        "robust_target": "log1p_arr_to_ber_min",
        "n_port_calls": int(len(model_df)),
    },
    "prediction_time": {
        "sample_definition": "latest AIS strictly before ARR",
        "constraint": "sample_time_utc < arr_time_utc",
        "max_ais_lag_minutes": 5,
    },
    "split": {
        "method": "70/15/15 stratified split at port_call_id level",
        "random_state": RANDOM_STATE,
        "train_port_calls": int((model_df["dataset_split"] == "train").sum()),
        "validation_port_calls": int(
            (model_df["dataset_split"] == "validation").sum()
        ),
        "test_port_calls": int((model_df["dataset_split"] == "test").sum()),
    },
    "numeric_features": numeric_features,
    "categorical_features": categorical_features,
    "excluded_columns": excluded_columns,
    "constant_features_removed": constant_features,
    "leakage_controls": [
        "AIS uses timestamp_utc <= sample_time_utc < arr_time_utc",
        "Port Status uses port_status_time_utc <= sample_time_utc",
        "ber_event_id, ber_time_utc, berth_zone and all post-ARR trajectory information are excluded",
    ],
}

model_df.to_parquet(OUTPUT_FILE, index=False)

with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
    json.dump(manifest, f, ensure_ascii=False, indent=2)

print("\n已保存模型数据：", OUTPUT_FILE.name)
print("已保存特征清单：", MANIFEST_FILE.name)
