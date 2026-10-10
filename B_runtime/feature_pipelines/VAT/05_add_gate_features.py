from pathlib import Path
from pipeline_paths import resolve_data_dir
import json
import numpy as np
import pandas as pd

DATA_DIR = resolve_data_dir(Path(__file__).resolve().parent)

SNAPSHOT_FILE = DATA_DIR / "04_high_snapshots_raw.parquet"
GEO_FILE = DATA_DIR / "yangshan_geofence_candidate_v2确定版.geojson"
OUTPUT_FILE = DATA_DIR / "05_high_snapshots_gate_features.parquet"

EARTH_RADIUS_KM = 6371.0088

# ---------- 1. 读取 snapshot 与固定 Arrival Gate ----------
snapshots = pd.read_parquet(SNAPSHOT_FILE)

with GEO_FILE.open("r", encoding="utf-8") as f:
    geojson = json.load(f)

gate_matches = [
    feature
    for feature in geojson["features"]
    if feature.get("properties", {}).get("name")
    == "Yangshan_Arrival_Gate_Line"
]

if len(gate_matches) != 1:
    raise ValueError(
        f"应只找到一个 Yangshan_Arrival_Gate_Line；当前为 {len(gate_matches)} 个。"
    )

gate_coordinates = gate_matches[0]["geometry"]["coordinates"]

if gate_matches[0]["geometry"]["type"] != "LineString":
    raise ValueError("Arrival Gate 必须是 LineString。")

(lon_1, lat_1), (lon_2, lat_2) = gate_coordinates

print("=== 固定 Arrival Gate ===")
print("端点 1：", (lon_1, lat_1))
print("端点 2：", (lon_2, lat_2))

# ---------- 2. 局部投影：用于 AIS 点到门线的最近距离 ----------
# 门线长度很短、研究区域集中在洋山港附近。
# 此局部等距近似足以计算 km 级距离，且不直接在经纬度“度”单位上量距离。
lon_0 = (lon_1 + lon_2) / 2
lat_0 = (lat_1 + lat_2) / 2
cos_lat_0 = np.cos(np.radians(lat_0))

def project_to_local_km(lon, lat):
    x = EARTH_RADIUS_KM * np.radians(lon - lon_0) * cos_lat_0
    y = EARTH_RADIUS_KM * np.radians(lat - lat_0)
    return x, y

def unproject_to_lonlat(x, y):
    lon = lon_0 + np.degrees(x / (EARTH_RADIUS_KM * cos_lat_0))
    lat = lat_0 + np.degrees(y / EARTH_RADIUS_KM)
    return lon, lat

# 门线两端的局部平面坐标
x1, y1 = project_to_local_km(lon_1, lat_1)
x2, y2 = project_to_local_km(lon_2, lat_2)

vx = x2 - x1
vy = y2 - y1
line_length_squared = vx**2 + vy**2

# 当前 AIS 点的局部平面坐标
x, y = project_to_local_km(
    snapshots["lon"].to_numpy(),
    snapshots["lat"].to_numpy(),
)

# 投影到线段上，得到最近点
t = ((x - x1) * vx + (y - y1) * vy) / line_length_squared
t = np.clip(t, 0, 1)

nearest_x = x1 + t * vx
nearest_y = y1 + t * vy

snapshots["distance_to_gate_km"] = np.sqrt(
    (x - nearest_x) ** 2 + (y - nearest_y) ** 2
)

nearest_lon, nearest_lat = unproject_to_lonlat(nearest_x, nearest_y)
snapshots["nearest_gate_lon"] = nearest_lon
snapshots["nearest_gate_lat"] = nearest_lat

# ---------- 3. 当前位置指向门线最近点的方位 ----------
lon_a = np.radians(snapshots["lon"].to_numpy())
lat_a = np.radians(snapshots["lat"].to_numpy())
lon_b = np.radians(nearest_lon)
lat_b = np.radians(nearest_lat)

delta_lon = lon_b - lon_a

bearing = np.degrees(
    np.arctan2(
        np.sin(delta_lon) * np.cos(lat_b),
        np.cos(lat_a) * np.sin(lat_b)
        - np.sin(lat_a) * np.cos(lat_b) * np.cos(delta_lon),
    )
)

snapshots["bearing_to_gate_deg"] = (bearing + 360) % 360

# COG 与指向门线的方位夹角，范围 0–180°
valid_cog = snapshots["cog_deg"].between(0, 360, inclusive="left")

cog_difference = np.abs(
    (
        snapshots["cog_deg"].to_numpy()
        - snapshots["bearing_to_gate_deg"].to_numpy()
        + 180
    ) % 360
    - 180
)

snapshots["heading_gate_difference_deg"] = np.where(
    valid_cog,
    cog_difference,
    np.nan,
)

# ---------- 4. 门线两侧 ----------
# GeoJSON 门线方向是端点 1 -> 端点 2（总体由北向南）。
# ARR 元数据标明为 east_to_west_inbound：
# 因而此固定方向下，线左侧的东侧定义为 Sea side，
# 西侧定义为 Port side。只使用该固定规则，不移动门线。
cross_product = vx * (y - y1) - vy * (x - x1)

snapshots["gate_side"] = np.where(
    cross_product >= 0,
    "sea_side",
    "port_side",
)

snapshots["gate_side_binary"] = np.where(
    cross_product >= 0,
    1,
    0,
)

# ---------- 5. 审计、保存 ----------
snapshot_count_by_arr = snapshots.groupby("arr_event_id").size()

print("\n=== Snapshot 完整性 ===")
print("Snapshot 总数：", len(snapshots))
print("ARR 事件数：", snapshots["arr_event_id"].nunique())
print("完整 36 个 snapshot 的 ARR 数：", int((snapshot_count_by_arr == 36).sum()))
print("少于 36 个 snapshot 的 ARR 数：", int((snapshot_count_by_arr < 36).sum()))

print("\n=== Gate 特征检查 ===")
print("distance_to_gate_km 范围：")
print(snapshots["distance_to_gate_km"].describe()[["min", "50%", "max"]])

print("\ngate_side 分布：")
print(snapshots["gate_side"].value_counts(dropna=False))

print("\nCOG 缺失或无效数：", int((~valid_cog).sum()))
print("SOG 缺失数：", int(snapshots["sog_knots"].isna().sum()))

snapshots.to_parquet(OUTPUT_FILE, index=False)

print(f"\n已保存：{OUTPUT_FILE.name}")
