from pathlib import Path
from pipeline_paths import resolve_data_dir
import json
import numpy as np
import pandas as pd
from shapely.geometry import Point, shape
from shapely.ops import transform
from pyproj import Transformer

DATA_DIR = resolve_data_dir(Path(__file__).resolve().parent / "项目")

INPUT_FILE = DATA_DIR / "36_arr_to_ber_snapshots_raw.parquet"
GEOJSON_FILE = DATA_DIR / "yangshan_geofence_candidate_v2确定版.geojson"
OUTPUT_FILE = DATA_DIR / "37_arr_to_ber_snapshots_spatial.parquet"

# 上海洋山港位置采用 UTM Zone 51N，用于米/公里距离计算。
WGS84 = "EPSG:4326"
LOCAL_CRS = "EPSG:32651"


def initial_bearing_deg(lon1, lat1, lon2, lat2):
    """由点 1 指向点 2 的初始方位角，范围 0–360°。"""
    lon1 = np.radians(lon1)
    lat1 = np.radians(lat1)
    lon2 = np.radians(lon2)
    lat2 = np.radians(lat2)

    dlon = lon2 - lon1
    y = np.sin(dlon) * np.cos(lat2)
    x = (
        np.cos(lat1) * np.sin(lat2)
        - np.sin(lat1) * np.cos(lat2) * np.cos(dlon)
    )
    return (np.degrees(np.arctan2(y, x)) + 360) % 360


def angular_difference_deg(angle_a, angle_b):
    """两个航向/方位角最小夹角，范围 0–180°。"""
    return np.abs((angle_a - angle_b + 180) % 360 - 180)


# 1. 读取已确认严格早于 ARR 的 snapshot。
snapshots = pd.read_parquet(INPUT_FILE)

if (snapshots["sample_time_utc"] >= snapshots["arr_time_utc"]).any():
    raise ValueError("发现 sample_time_utc >= arr_time_utc，停止处理。")

print("=== Snapshot 完整性 ===")
print("样本数：", len(snapshots))
print("Port Call 数：", snapshots["port_call_id"].nunique())
print(
    "sample_time < ARR 违规数：",
    (snapshots["sample_time_utc"] >= snapshots["arr_time_utc"]).sum()
)

# 2. 从确定版 GeoJSON 读取固定空间对象。
with open(GEOJSON_FILE, "r", encoding="utf-8") as f:
    geojson = json.load(f)

geometry_by_name = {
    feature["properties"]["name"]: shape(feature["geometry"])
    for feature in geojson["features"]
}

required_geometries = [
    "Yangshan_Arrival_Gate_Line",
    "Yangshan_Berth_Zone_Main",
    "Yangshan_Berth_Zone_West",
]
missing = [name for name in required_geometries if name not in geometry_by_name]
if missing:
    raise KeyError(f"GeoJSON 缺少空间对象：{missing}")

gate_line = geometry_by_name["Yangshan_Arrival_Gate_Line"]
main_zone = geometry_by_name["Yangshan_Berth_Zone_Main"]
west_zone = geometry_by_name["Yangshan_Berth_Zone_West"]

print("\n=== 使用的固定空间对象 ===")
print("Arrival Gate：", gate_line.geom_type)
print("Main Berth Zone：", main_zone.geom_type)
print("West Berth Zone：", west_zone.geom_type)

# 3. 在局部投影坐标系中计算距离。
# 洋山港位于 UTM Zone 51N；投影后单位为米。
transformer = Transformer.from_crs(
    WGS84, LOCAL_CRS, always_xy=True
).transform

gate_local = transform(transformer, gate_line)
main_local = transform(transformer, main_zone)
west_local = transform(transformer, west_zone)

points_local = [
    transform(transformer, Point(lon, lat))
    for lon, lat in zip(snapshots["lon"], snapshots["lat"])
]

snapshots["distance_to_arrival_gate_km"] = [
    point.distance(gate_local) / 1000
    for point in points_local
]

snapshots["distance_to_main_berth_zone_km"] = [
    point.distance(main_local) / 1000
    for point in points_local
]

snapshots["distance_to_west_berth_zone_km"] = [
    point.distance(west_local) / 1000
    for point in points_local
]

snapshots["is_in_main_berth_zone"] = [
    int(point.within(main_local))
    for point in points_local
]

snapshots["is_in_west_berth_zone"] = [
    int(point.within(west_local))
    for point in points_local
]

# 4. 当前位置相对 Arrival Gate 的方向特征。
gate_midpoint = gate_line.interpolate(0.5, normalized=True)
gate_lon = gate_midpoint.x
gate_lat = gate_midpoint.y

snapshots["bearing_to_arrival_gate_deg"] = initial_bearing_deg(
    snapshots["lon"].to_numpy(),
    snapshots["lat"].to_numpy(),
    gate_lon,
    gate_lat,
)

valid_cog = snapshots["cog_deg"].between(0, 359.999)
snapshots["heading_gate_difference_deg"] = np.nan
snapshots.loc[valid_cog, "heading_gate_difference_deg"] = angular_difference_deg(
    snapshots.loc[valid_cog, "cog_deg"].to_numpy(),
    snapshots.loc[valid_cog, "bearing_to_arrival_gate_deg"].to_numpy(),
)

# 注意：不使用 berth_zone、BER event_time、BER duration 等结果字段。
spatial_features = [
    "distance_to_arrival_gate_km",
    "distance_to_main_berth_zone_km",
    "distance_to_west_berth_zone_km",
    "is_in_main_berth_zone",
    "is_in_west_berth_zone",
    "bearing_to_arrival_gate_deg",
    "heading_gate_difference_deg",
]

print("\n=== 空间特征检查 ===")
print(snapshots[spatial_features].describe().T[["min", "50%", "max"]])

print("\n泊位区内 snapshot 数：")
print(
    snapshots[
        ["is_in_main_berth_zone", "is_in_west_berth_zone"]
    ].sum()
)

print("\nCOG 无效或缺失s数：", (~valid_cog).sum())

snapshots.to_parquet(OUTPUT_FILE, index=False)

print("\n已保存：", OUTPUT_FILE.name)
