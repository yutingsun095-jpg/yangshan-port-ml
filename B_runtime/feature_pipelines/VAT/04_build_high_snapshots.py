from pathlib import Path
from pipeline_paths import resolve_data_dir
import pandas as pd
import duckdb

DATA_DIR = resolve_data_dir(Path(__file__).resolve().parent)

AIS_FILE = DATA_DIR / "stage2_yangshan_202401.parquet"
ARR_FILE = DATA_DIR / "stage3_yangshan_arr_final_202401确定版.csv"

# 已根据覆盖率审计确定的主窗口
MAX_LEAD_MINUTES = 6 * 60
SNAPSHOT_INTERVAL_MINUTES = 10
MAX_LOOKBACK_MINUTES = 30

# ---------- 1. 只读取 high ARR ----------
arr = pd.read_csv(ARR_FILE)
arr["mmsi"] = arr["mmsi"].astype(str)
arr["event_time_utc"] = pd.to_datetime(arr["event_time_utc"], utc=True)

high_arr = arr.loc[
    arr["confidence_level"].eq("high"),
    ["event_id", "mmsi", "event_time_utc", "confidence_level"]
].copy()

high_arr = high_arr.rename(
    columns={
        "event_id": "arr_event_id",
        "event_time_utc": "arr_time_utc",
    }
)

# ---------- 2. 每个 ARR 生成 10 分钟间隔的目标预测时刻 ----------
lead_minutes = list(
    range(
        SNAPSHOT_INTERVAL_MINUTES,
        MAX_LEAD_MINUTES + 1,
        SNAPSHOT_INTERVAL_MINUTES,
    )
)

lead_table = pd.DataFrame({"planned_lead_minutes": lead_minutes})

targets = (
    high_arr.merge(lead_table, how="cross")
    .assign(
        target_snapshot_time_utc=lambda x: (
            x["arr_time_utc"]
            - pd.to_timedelta(x["planned_lead_minutes"], unit="min")
        )
    )
)

print("=== Snapshot 目标网格 ===")
print("high ARR 数：", high_arr["arr_event_id"].nunique())
print("每个 ARR 的计划 snapshot 数：", len(lead_minutes))
print("目标 snapshot 总数：", len(targets))

# ---------- 3. 在每个目标时刻之前选择最近的有效 AIS ----------
# ASOF JOIN 的含义：
# 对每个 target_snapshot_time_utc，选择同 MMSI 且 timestamp_utc <= 目标时刻的最近 AIS。
con = duckdb.connect()
con.register("targets", targets)

query = """
WITH valid_ais AS (
    SELECT
        CAST(mmsi AS VARCHAR) AS mmsi,
        timestamp_utc,
        lon,
        lat,
        sog_knots,
        cog_deg,
        heading_deg,
        nav_status
    FROM read_parquet(?)
    WHERE
        COALESCE(qc_malformed, FALSE) = FALSE
        AND COALESCE(qc_invalid_position, FALSE) = FALSE
        AND COALESCE(qc_duplicate, FALSE) = FALSE
        AND timestamp_utc IS NOT NULL
        AND mmsi IN (SELECT DISTINCT mmsi FROM targets)
)
SELECT
    t.arr_event_id,
    t.mmsi,
    t.arr_time_utc,
    t.confidence_level,
    t.planned_lead_minutes,
    t.target_snapshot_time_utc,

    a.timestamp_utc AS sample_time_utc,
    a.lon,
    a.lat,
    a.sog_knots,
    a.cog_deg,
    a.heading_deg,
    a.nav_status
FROM targets AS t
ASOF LEFT JOIN valid_ais AS a
    ON t.mmsi = a.mmsi
    AND t.target_snapshot_time_utc >= a.timestamp_utc
"""

snapshots = con.execute(query, [str(AIS_FILE)]).df()
con.close()

# ---------- 4. 严格筛选、计算真实 VAT 标签 ----------
snapshots["arr_time_utc"] = pd.to_datetime(
    snapshots["arr_time_utc"], utc=True
)
snapshots["target_snapshot_time_utc"] = pd.to_datetime(
    snapshots["target_snapshot_time_utc"], utc=True
)
snapshots["sample_time_utc"] = pd.to_datetime(
    snapshots["sample_time_utc"], utc=True
)

# 没找到 AIS 的目标时刻不能形成样本
snapshots = snapshots.dropna(subset=["sample_time_utc"]).copy()

# sample_time 必须不晚于目标时刻，且最多向前回看 30 分钟
snapshots["snapshot_lag_minutes"] = (
    snapshots["target_snapshot_time_utc"]
    - snapshots["sample_time_utc"]
).dt.total_seconds() / 60

snapshots = snapshots.loc[
    (snapshots["snapshot_lag_minutes"] >= 0)
    & (snapshots["snapshot_lag_minutes"] <= MAX_LOOKBACK_MINUTES)
].copy()

# 最终 VAT 标签：严格由实际样本时刻到 ARR 时刻计算
snapshots["remaining_time_to_arr_min"] = (
    snapshots["arr_time_utc"]
    - snapshots["sample_time_utc"]
).dt.total_seconds() / 60

# 安全检查：绝不允许 ARR 后 AIS 混入
snapshots = snapshots.loc[
    snapshots["remaining_time_to_arr_min"] > 0
].copy()

# 若一个 AIS 被多个相邻 10 分钟网格重复选中，只保留一次；
# 优先保留离目标时刻最近的那一次。
snapshots = (
    snapshots.sort_values("snapshot_lag_minutes")
    .drop_duplicates(
        subset=["arr_event_id", "sample_time_utc"],
        keep="first",
    )
    .sort_values(["arr_time_utc", "sample_time_utc"])
    .reset_index(drop=True)
)

# ---------- 5. 输出与保存 ----------
output_file = DATA_DIR / "04_high_snapshots_raw.parquet"
snapshots.to_parquet(output_file, index=False)

arr_with_snapshot = snapshots["arr_event_id"].nunique()

print("\n=== Snapshot 构造结果 ===")
print("保留 snapshot 数：", len(snapshots))
print("至少有一个 snapshot 的 high ARR 数：", arr_with_snapshot)
print(
    "ARR 覆盖率：",
    f"{arr_with_snapshot / high_arr['arr_event_id'].nunique() * 100:.2f}%"
)
print(
    "VAT 范围（分钟）：",
    f"{snapshots['remaining_time_to_arr_min'].min():.2f}",
    "至",
    f"{snapshots['remaining_time_to_arr_min'].max():.2f}",
)
print(
    "每个 ARR 的 snapshot 数（最少 / 中位数 / 最多）：",
    int(snapshots.groupby("arr_event_id").size().min()),
    int(snapshots.groupby("arr_event_id").size().median()),
    int(snapshots.groupby("arr_event_id").size().max()),
)
print("\n已保存：", output_file.name)
print("\n前五行：")
print(
    snapshots[
        [
            "arr_event_id",
            "mmsi",
            "sample_time_utc",
            "arr_time_utc",
            "remaining_time_to_arr_min",
            "lon",
            "lat",
            "sog_knots",
        ]
    ].head()
)
