"""Read-only Folium view of released geometry, AIS and formal event coordinates."""

import json
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Tuple

import folium
import numpy as np
import pandas as pd
from branca.element import Element
from shapely.geometry import Point, shape


MAX_MAP_POINTS = 1200
EXPECTED_OBJECTS = {
    "Yangshan_No1_Anchorage", "Yangshan_No1_Pilot_Point",
    "Yangshan_No2_Pilot_Point", "Yangshan_External_Approach_Area",
    "Yangshan_Arrival_Gate_Line", "Yangshan_Berth_Zone_Main",
    "Yangshan_Berth_Zone_West",
}
ZONE_NAMES = {
    "Main": "Yangshan_Berth_Zone_Main",
    "West": "Yangshan_Berth_Zone_West",
}
EVENT_COLORS = {"ANC": "#7353ba", "ARR": "#188b56", "UNB": "#e16b2e", "DEP": "#d62f4b"}
VIEW_OBJECTS = {
    "Yangshan_No1_Anchorage", "Yangshan_Arrival_Gate_Line",
    "Yangshan_Berth_Zone_Main", "Yangshan_Berth_Zone_West",
}


def read_geofence(path: Path) -> Dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as stream:
        data = json.load(stream)
    if data.get("type") != "FeatureCollection" or not isinstance(data.get("features"), list):
        raise ValueError("Expected a GeoJSON FeatureCollection")
    return data


def geofence_object_names(path: Path) -> List[str]:
    data = read_geofence(path)
    return [str(feature.get("properties", {}).get("name", ""))
            for feature in data["features"]]


def _coords(node: Any):
    if isinstance(node, (tuple, list)):
        if len(node) >= 2 and all(isinstance(x, (int, float)) for x in node[:2]):
            yield float(node[0]), float(node[1])
        else:
            for child in node:
                yield from _coords(child)


def display_track(ais: pd.DataFrame, max_points: int = MAX_MAP_POINTS) -> pd.DataFrame:
    """Evenly thin only the map copy; preserve first and last AIS point."""
    if max_points < 2:
        raise ValueError("Map display must retain at least start and end")
    ordered = ais.sort_values("timestamp_utc", kind="stable").reset_index(drop=True)
    if len(ordered) <= max_points:
        return ordered.copy()
    indices = np.linspace(0, len(ordered) - 1, max_points, dtype=int)
    return ordered.iloc[indices].copy().reset_index(drop=True)


def _valid_coord(lon: Any, lat: Any) -> bool:
    return pd.notna(lon) and pd.notna(lat) and -180 <= float(lon) <= 180 and -90 <= float(lat) <= 90


def _utc_text(value: Any) -> str:
    if pd.isna(value):
        return "—"
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC").isoformat() if stamp.tzinfo is None else stamp.tz_convert("UTC").isoformat()


def _number(value: Any, digits: int = 3) -> str:
    return "—" if pd.isna(value) else "{:.{n}f}".format(float(value), n=digits)


def _event_tooltip(event: pd.Series) -> str:
    items = [
        ("event_type", event.event_type),
        ("event_time_utc", _utc_text(event.event_time_utc)),
        ("confidence_level", event.confidence_level),
        ("qc_flag", event.qc_flag),
    ]
    if event.event_type == "ANC":
        items.extend([
            ("event_end_time", _utc_text(event.event_end_time_utc)),
            ("duration_min", _number(event.duration_min, 2)),
        ])
    if str(event.get("replay_status", "")) == "future":
        items.append(("replay_note", "Ground truth future event — not available as online input"))
    return "<br>".join("{}: {}".format(escape(str(key)), escape(str(value))) for key, value in items)


def current_spatial_region(geofence_path: Path, lon: Any, lat: Any) -> str:
    """Polygon containment only; never infer or update a formal event."""
    if not _valid_coord(lon, lat):
        return "Other"
    point = Point(float(lon), float(lat))
    features = read_geofence(geofence_path)["features"]
    region_names = [
        ("Yangshan_Berth_Zone_Main", "Main"),
        ("Yangshan_Berth_Zone_West", "West"),
        ("Yangshan_No1_Anchorage", "No.1 Anchorage"),
        ("Yangshan_External_Approach_Area", "External Approach"),
    ]
    by_name = {item.get("properties", {}).get("name"): item for item in features}
    for name, display_name in region_names:
        if name in by_name and shape(by_name[name]["geometry"]).covers(point):
            return display_name
    return "Other"


def build_port_call_map(geofence_path: Path, ais: pd.DataFrame,
                        events: pd.DataFrame, max_points: int = MAX_MAP_POINTS,
                        replay_time_utc: Any = None,
                        include_future_truth: bool = False
                        ) -> Tuple[folium.Map, Dict[str, Any]]:
    """Render original geometry; in replay mode forbid future AIS by construction."""
    moment = None
    if replay_time_utc is not None:
        moment = pd.Timestamp(replay_time_utc)
        if moment.tzinfo is None:
            raise ValueError("Replay time must be UTC-aware")
        moment = moment.tz_convert("UTC")
        if not ais.empty:
            ais = ais.loc[pd.to_datetime(ais.timestamp_utc, utc=True) <= moment].copy()
        events = events.copy()
        event_times = pd.to_datetime(events.event_time_utc, utc=True)
        if not include_future_truth:
            events = events.loc[event_times <= moment].copy()
        else:
            events["replay_status"] = np.where(event_times > moment, "future", "past")
    geojson = read_geofence(geofence_path)
    features = geojson["features"]
    names = [str(item.get("properties", {}).get("name", "")) for item in features]
    if len(features) != 7 or set(names) != EXPECTED_OBJECTS:
        raise ValueError("Expected the seven frozen Yangshan spatial objects")
    shown = display_track(ais, max_points=max_points)
    valid_shown = shown.loc[shown.apply(lambda row: _valid_coord(row.lon, row.lat), axis=1)]
    bounds_coords = [pair for feature in features
                     if feature["properties"]["name"] in VIEW_OBJECTS
                     for pair in _coords(feature["geometry"]["coordinates"])]
    bounds_coords.extend((float(row.lon), float(row.lat))
                         for row in valid_shown.itertuples(index=False))
    if not bounds_coords:
        raise ValueError("Frozen GeoJSON has no coordinates")
    min_lon, max_lon = min(lon for lon, _ in bounds_coords), max(lon for lon, _ in bounds_coords)
    min_lat, max_lat = min(lat for _, lat in bounds_coords), max(lat for _, lat in bounds_coords)
    map_view = folium.Map(location=[(min_lat + max_lat) / 2, (min_lon + max_lon) / 2],
                          zoom_start=10, zoom_snap=0.25,
                          tiles="OpenStreetMap", control_scale=True)
    base_layer = folium.FeatureGroup(name="确定版GeoJSON · 7对象", show=True)
    for feature in features:
        name = str(feature["properties"]["name"])
        geometry = feature["geometry"]
        if geometry["type"] == "Point":
            lon, lat = geometry["coordinates"][:2]
            folium.CircleMarker([lat, lon], radius=4, color="#7a8795", fill=True,
                                fill_opacity=1, tooltip=folium.Tooltip(escape(name))).add_to(base_layer)
        else:
            color = ("#7353ba" if name.endswith("Anchorage") else
                     "#2459a6" if "Berth_Zone" in name else
                     "#2f8b73" if "Arrival_Gate" in name else "#aebdc9")
            folium.GeoJson(feature, name=name,
                           style_function=lambda _f, c=color, n=name, g=geometry["type"]: {
                               "color": c, "weight": 3 if n.endswith("Anchorage") else
                               2.5 if "Arrival_Gate" in n else 2,
                               "fillOpacity": .025 if "External_Approach" in n else
                               .06 if g == "Polygon" else 0,
                           },
                           tooltip=folium.GeoJsonTooltip(fields=["name"], labels=False)).add_to(base_layer)
    base_layer.add_to(map_view)

    highlighted = []
    for event in events.loc[events.event_type.eq("BER")].itertuples(index=False):
        zone_name = ZONE_NAMES.get(str(event.berth_zone))
        if not zone_name:
            raise ValueError("BER has unknown berth_zone: {}".format(event.berth_zone))
        zone_feature = next(item for item in features if item["properties"]["name"] == zone_name)
        future = getattr(event, "replay_status", "") == "future"
        folium.GeoJson(zone_feature, name=("未来真值BER · " if future else "BER区域高亮 · ") + zone_name,
                       style_function=lambda _f, is_future=future: {
                           "color": "#969696" if is_future else "#e6a100", "weight": 5,
                           "fillColor": "#bababa" if is_future else "#f4bb45",
                           "fillOpacity": .12 if is_future else .24},
                       tooltip=folium.Tooltip(_event_tooltip(pd.Series(event._asdict())))).add_to(map_view)
        highlighted.append(zone_name)

    track_layer = folium.FeatureGroup(name="AIS轨迹（地图抽稀副本）", show=True)
    if not valid_shown.empty:
        folium.PolyLine(valid_shown[["lat", "lon"]].values.tolist(), color="#166da7",
                        weight=3.5, opacity=.88).add_to(track_layer)
        for point in valid_shown.itertuples(index=False):
            tooltip = "<br>".join([
                "MMSI: " + escape(str(point.mmsi)),
                "timestamp UTC: " + escape(_utc_text(point.timestamp_utc)),
                "SOG: " + _number(point.sog_knots, 2) + " kn",
                "COG: " + _number(point.cog_deg, 2) + "°",
                "lon: " + _number(point.lon, 6),
                "lat: " + _number(point.lat, 6),
            ])
            folium.CircleMarker([point.lat, point.lon], radius=2, color="#1870bc",
                                fill=True, fill_opacity=.65,
                                tooltip=folium.Tooltip(tooltip)).add_to(track_layer)
        marker_points = [("轨迹起点", valid_shown.iloc[0], "green")]
        if moment is None:
            marker_points.append(("轨迹终点", valid_shown.iloc[-1], "red"))
        else:
            last_actual = ais.sort_values("timestamp_utc", kind="stable").iloc[-1]
            if _valid_coord(last_actual.lon, last_actual.lat):
                marker_points.append(("当前最新AIS", last_actual, "red"))
        for label, point, color in marker_points:
            folium.Marker([point.lat, point.lon], tooltip=label + " · " + _utc_text(point.timestamp_utc),
                          icon=folium.Icon(color=color)).add_to(track_layer)
    track_layer.add_to(map_view)

    event_layer = folium.FeatureGroup(name="正式事件坐标（BER只高亮区域）", show=True)
    mapped = []
    mapped_coordinates = {}
    for _, event in events.iterrows():
        kind = str(event.event_type)
        if kind == "BER":
            continue  # Formal BER has no single released event coordinate.
        lon, lat = ((event.centroid_lon, event.centroid_lat) if kind == "ANC"
                    else (event.lon, event.lat))
        if not _valid_coord(lon, lat):
            raise ValueError("Formal {} lacks its released coordinate".format(kind))
        future = event.get("replay_status", "") == "future"
        folium.CircleMarker([lat, lon], radius=8, color="white", weight=2,
                            fill=True, fill_color="#999999" if future else EVENT_COLORS[kind],
                            fill_opacity=.55 if future else 1,
                            tooltip=folium.Tooltip(_event_tooltip(event))).add_to(event_layer)
        mapped.append(kind)
        mapped_coordinates[str(event.event_id)] = [float(lon), float(lat)]
    event_layer.add_to(map_view)
    folium.LayerControl(collapsed=True).add_to(map_view)
    map_view.get_root().html.add_child(Element("""
    <div style="position:fixed;z-index:9999;left:12px;bottom:14px;max-width:310px;
      background:rgba(255,255,255,.94);border:1px solid #cbd5df;border-radius:8px;
      padding:9px 12px;font:12px/1.45 sans-serif;color:#233346;box-shadow:0 2px 8px #0002">
      <b>地图图例</b><br>
      <span style="color:#166da7">━━</span> AIS历史轨迹　
      <span style="color:#d33932">●</span> 当前船位<br>
      <span style="color:#7353ba">●</span> ANC　
      <span style="color:#188b56">●</span> ARR　
      <span style="color:#e6a100">▰</span> BER区域<br>
      <span style="color:#e16b2e">●</span> UNB　
      <span style="color:#d62f4b">●</span> DEP<br>
      <span style="color:#2f8b73">━━</span> Arrival Gate　
      <span style="color:#7353ba">━━</span> No.1 Anchorage
    </div>"""))
    # Wait for the nested Streamlit iframe to acquire its real size before
    # fitting bounds; a pre-layout fitBounds can otherwise zoom far out.
    view_bounds = [[min_lat, min_lon], [max_lat, max_lon]]
    map_name = map_view.get_name()
    map_view.get_root().script.add_child(Element("""
    (function () {
      var map = %(map_name)s;
      var bounds = %(bounds)s;
      var previousWidth = 0;
      function fitVisibleArea() {
        map.invalidateSize({pan: false});
        var size = map.getSize();
        if (size.x < 320 || size.y < 300) return;
        if (Math.abs(size.x - previousWidth) < 2) return;
        previousWidth = size.x;
        map.fitBounds(bounds, {padding: [18, 18], animate: false, maxZoom: 13});
      }
      window.addEventListener('load', function () {
        setTimeout(fitVisibleArea, 100);
        setTimeout(fitVisibleArea, 500);
        if (window.ResizeObserver) {
          new ResizeObserver(fitVisibleArea).observe(map.getContainer());
        }
      });
    })();
    """ % {"map_name": map_name, "bounds": json.dumps(view_bounds)}))
    max_ais_time = None if ais.empty else pd.to_datetime(ais.timestamp_utc, utc=True).max()
    if moment is not None and max_ais_time is not None and max_ais_time > moment:
        raise AssertionError("Map contains future AIS")
    return map_view, {
        "geojson_objects": names, "ais_queried_count": len(ais),
        "ais_display_count": len(shown), "ais_valid_display_count": len(valid_shown),
        "mapped_event_types": mapped, "highlighted_berth_zones": highlighted,
        "mapped_event_coordinates": mapped_coordinates,
        "initial_view_bounds": view_bounds,
        "max_ais_time_utc": max_ais_time,
        "future_truth_event_count": int(events.get("replay_status", pd.Series(dtype=str)).eq("future").sum()),
        "ber_coordinate_fabricated": False,
    }
