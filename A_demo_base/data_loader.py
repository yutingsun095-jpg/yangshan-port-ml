"""Read-only access to the six packaged public A-module Demo inputs.

The public AIS data is the union of every released Port Call's validated local
Demo query window. It is never loaded wholesale into Pandas. A selected vessel
trajectory is queried from one or more small Parquet shards with DuckDB using
both an MMSI predicate and a bounded UTC time range.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import duckdb
import pandas as pd
import pyarrow.parquet as pq


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
AIS_GLOB = DATA_DIR / "demo_ais_all_port_calls_*.parquet"
SOURCES = {
    "ais": AIS_GLOB,
    "geofence": DATA_DIR / "yangshan_geofence_candidate_v2确定版.geojson",
    "port_calls": DATA_DIR / "stage4_yangshan_port_calls_final_202401确定版.parquet",
    "labels": DATA_DIR / "stage4_yangshan_direct_indirect_labels_202401确定版.parquet",
    "port_status": DATA_DIR / "stage5_yangshan_port_status_10min_202401确定版.parquet",
    "events": DATA_DIR / "stage6_yangshan_vessel_events_202401确定版.parquet",
}
TIME_COLUMNS = {
    "ais": "timestamp_utc",
    "port_calls": "arr_time_utc",
    "labels": "arr_time_utc",
    "port_status": "snapshot_time_utc",
    "events": "event_time_utc",
}
CORE_COLUMNS = {
    "ais": ["mmsi", "timestamp_utc", "lon", "lat", "sog_knots", "nav_status"],
    "port_calls": ["port_call_id", "arr_event_id", "mmsi", "arr_time_utc", "ber_event_id", "unb_event_id", "dep_event_id"],
    "labels": ["port_call_id", "mmsi", "arr_event_id", "final_label", "label_confidence"],
    "port_status": ["snapshot_time_utc", "active_vessel_count", "anchorage_confirmed_waiting_count", "berth_total_confirmed_occupied_count"],
    "events": ["event_id", "event_type", "mmsi", "event_time_utc", "port_call_id", "confidence_level"],
}
AIS_QUERY_COLUMNS = ["mmsi", "timestamp_utc", "lon", "lat", "sog_knots", "nav_status", "cog_deg"]


def utc_connection():
    """Create an in-memory DuckDB connection that renders TIMESTAMPTZ as UTC."""
    connection = duckdb.connect(database=":memory:")
    connection.execute("SET TimeZone='UTC'")
    return connection


def _require_inputs() -> None:
    missing = [str(path) for name, path in SOURCES.items()
               if name != "ais" and not path.is_file()]
    if not list(DATA_DIR.glob(AIS_GLOB.name)):
        missing.append(str(AIS_GLOB))
    if missing:
        raise FileNotFoundError("Missing frozen Demo inputs: " + "; ".join(missing))


def _utc_datetime(value: Any) -> datetime:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise ValueError("A timezone-aware UTC timestamp is required")
    return timestamp.tz_convert("UTC").to_pydatetime()


def _ais_time_range_from_footer(parquet_files: List[pq.ParquetFile]) -> Tuple[Optional[str], Optional[str]]:
    lows: List[datetime] = []
    highs: List[datetime] = []
    for parquet_file in parquet_files:
        field_index = parquet_file.schema_arrow.names.index("timestamp_utc")
        for index in range(parquet_file.num_row_groups):
            statistics = parquet_file.metadata.row_group(index).column(field_index).statistics
            if statistics is None or not statistics.has_min_max:
                return None, None
            lows.append(statistics.min)
            highs.append(statistics.max)
    return str(min(lows)), str(max(highs))


def inspect_sources() -> Dict[str, Dict[str, Any]]:
    """Inspect all six files; use Parquet metadata for the 52M-row AIS file."""
    _require_inputs()
    result: Dict[str, Dict[str, Any]] = {}
    with utc_connection() as connection:
        for name, path in SOURCES.items():
            if name == "geofence":
                with path.open("r", encoding="utf-8") as stream:
                    geofence = json.load(stream)
                features = geofence.get("features", [])
                if geofence.get("type") != "FeatureCollection" or not features:
                    raise ValueError("GeoJSON is not a nonempty FeatureCollection")
                result[name] = {
                    "path": str(path), "rows": len(features),
                    "core_fields": ["type", "geometry", "properties.name"],
                    "fields": ["type", "features", "geometry", "properties"],
                    "time_min_utc": None, "time_max_utc": None,
                    "objects": [str(feature.get("properties", {}).get("name", ""))
                                for feature in features],
                }
                continue
            if name == "ais":
                ais_paths = sorted(DATA_DIR.glob(AIS_GLOB.name))
                parquet_files = [pq.ParquetFile(item) for item in ais_paths]
                fields = parquet_files[0].schema_arrow.names
                if any(item.schema_arrow.names != fields for item in parquet_files[1:]):
                    raise ValueError("Public AIS shard schemas disagree")
            else:
                parquet_file = pq.ParquetFile(path)
                fields = parquet_file.schema_arrow.names
            absent = set(CORE_COLUMNS[name]) - set(fields)
            if absent:
                raise ValueError("Missing core fields in {}: {}".format(name, sorted(absent)))
            if name == "ais":
                first, last = _ais_time_range_from_footer(parquet_files)
                if first is None:
                    first, last = connection.execute(
                        "SELECT MIN(timestamp_utc), MAX(timestamp_utc) FROM read_parquet(?)",
                        [str(path)]).fetchone()
                count = sum(item.metadata.num_rows for item in parquet_files)
            else:
                column = TIME_COLUMNS[name]
                count, first, last = connection.execute(
                    "SELECT COUNT(*), MIN({0}), MAX({0}) FROM read_parquet(?)".format(column),
                    [str(path)]).fetchone()
                if count != parquet_file.metadata.num_rows:
                    raise ValueError("Parquet footer/query row mismatch: " + name)
            result[name] = {
                "path": str(path), "rows": int(count),
                "core_fields": CORE_COLUMNS[name], "fields": fields,
                "time_min_utc": str(first), "time_max_utc": str(last),
            }
    return result


def validate_links() -> Dict[str, int]:
    """Check released Port Call, label and event relationships without inference."""
    _require_inputs()
    # These three released tables are small (712, 712 and 1600 rows).
    calls = pd.read_parquet(SOURCES["port_calls"])
    labels = pd.read_parquet(SOURCES["labels"])
    events = pd.read_parquet(SOURCES["events"])
    if calls.port_call_id.isna().any() or not calls.port_call_id.is_unique:
        raise ValueError("Port Call IDs are missing or duplicated")
    if labels.port_call_id.isna().any() or not labels.port_call_id.is_unique:
        raise ValueError("Label Port Call IDs are missing or duplicated")
    if events.event_id.isna().any() or not events.event_id.is_unique:
        raise ValueError("Event IDs are missing or duplicated")
    join = calls[["port_call_id", "mmsi", "arr_event_id"]].merge(
        labels[["port_call_id", "mmsi", "arr_event_id"]],
        on="port_call_id", suffixes=("_call", "_label"), how="outer",
        indicator=True, validate="one_to_one")
    if not join._merge.eq("both").all():
        raise ValueError("A Port Call does not have exactly one label row")
    if not join.mmsi_call.astype(str).eq(join.mmsi_label.astype(str)).all() or not join.arr_event_id_call.eq(join.arr_event_id_label).all():
        raise ValueError("Port Call and label MMSI/ARR identities disagree")
    linked = events.loc[events.port_call_id.notna()]
    event_join = linked[["event_id", "event_type", "mmsi", "port_call_id"]].merge(
        calls[["port_call_id", "mmsi", "arr_event_id", "ber_event_id", "unb_event_id", "dep_event_id"]],
        on="port_call_id", how="left", suffixes=("_event", "_call"),
        validate="many_to_one", indicator=True)
    if not event_join._merge.eq("both").all() or not event_join.mmsi_event.astype(str).eq(event_join.mmsi_call.astype(str)).all():
        raise ValueError("An event has an invalid Port Call or MMSI link")
    event_key = {"ARR": "arr_event_id", "BER": "ber_event_id",
                 "UNB": "unb_event_id", "DEP": "dep_event_id"}
    for kind, field in event_key.items():
        part = event_join.loc[event_join.event_type.eq(kind)]
        if not part.event_id.eq(part[field]).all():
            raise ValueError("{} event ID differs from its Port Call chain".format(kind))
    anc = event_join.loc[event_join.event_type.eq("ANC")]
    label_by_call = labels.set_index("port_call_id")
    for row in anc.itertuples(index=False):
        published = label_by_call.loc[row.port_call_id, "linked_anc_event_id"]
        ids = [] if pd.isna(published) else [item.strip() for item in str(published).replace(";", "|").split("|")]
        if row.event_id not in ids:
            raise ValueError("ANC Port Call ID is not present in the published label link")
    return {
        "port_calls": len(calls), "labels_joined": len(join),
        "events": len(events), "events_with_port_call_id": len(linked),
        "anc_linked": len(anc), "anc_unlinked": int(events.loc[events.event_type.eq("ANC"), "port_call_id"].isna().sum()),
    }


def pick_sample_port_call(seed: int = 202401) -> str:
    """Select reproducibly among complete chains, preferring a linked ANC."""
    with utc_connection() as connection:
        rows = connection.execute(
            "SELECT port_call_id FROM read_parquet(?) "
            "WHERE arr_event_id IS NOT NULL AND ber_event_id IS NOT NULL "
            "AND unb_event_id IS NOT NULL AND dep_event_id IS NOT NULL "
            "AND linked_anc_count > 0",
            [str(SOURCES["port_calls"])]).fetchall()
        if not rows:
            rows = connection.execute(
                "SELECT port_call_id FROM read_parquet(?) "
                "WHERE arr_event_id IS NOT NULL AND ber_event_id IS NOT NULL "
                "AND unb_event_id IS NOT NULL AND dep_event_id IS NOT NULL",
                [str(SOURCES["port_calls"])]).fetchall()
    if not rows:
        raise ValueError("No complete ARR/BER/UNB/DEP Port Call available")
    return random.Random(seed).choice([row[0] for row in rows])


def get_port_call_bundle(port_call_id: str) -> Dict[str, Any]:
    """Read one released Port Call, its released label and linked formal events."""
    if not port_call_id:
        raise ValueError("port_call_id is required")
    with utc_connection() as connection:
        call = connection.execute(
            "SELECT * FROM read_parquet(?) WHERE port_call_id = ? LIMIT 1",
            [str(SOURCES["port_calls"]), port_call_id]).fetchdf()
        label = connection.execute(
            "SELECT * FROM read_parquet(?) WHERE port_call_id = ? LIMIT 1",
            [str(SOURCES["labels"]), port_call_id]).fetchdf()
        events = connection.execute(
            "SELECT * FROM read_parquet(?) WHERE port_call_id = ? "
            "ORDER BY event_time_utc, event_type",
            [str(SOURCES["events"]), port_call_id]).fetchdf()
    if len(call) != 1 or len(label) != 1:
        raise KeyError("Port Call missing in calls or labels: " + port_call_id)
    if events.empty or not events.event_type.eq("ARR").any():
        raise ValueError("Port Call has no linked formal ARR: " + port_call_id)
    return {"port_call": call.iloc[0].to_dict(), "label": label.iloc[0].to_dict(),
            "events": events}


def list_mmsis() -> List[str]:
    """List only MMSIs represented in the released Port Call table."""
    with utc_connection() as connection:
        rows = connection.execute(
            "SELECT DISTINCT mmsi FROM read_parquet(?) WHERE mmsi IS NOT NULL ORDER BY mmsi",
            [str(SOURCES["port_calls"])]).fetchall()
    return [str(row[0]) for row in rows]


def list_port_calls(mmsi: str) -> pd.DataFrame:
    """Return published visits for exactly one MMSI."""
    with utc_connection() as connection:
        return connection.execute(
            "SELECT port_call_id, mmsi, arr_time_utc, ber_event_id, unb_event_id, dep_event_id "
            "FROM read_parquet(?) WHERE mmsi = ? ORDER BY arr_time_utc",
            [str(SOURCES["port_calls"]), mmsi]).fetchdf()


def query_visit_ais(mmsi: str, start_utc: Any, end_utc: Any) -> pd.DataFrame:
    """Read the selected visit's AIS only, filtered by MMSI and UTC time.

    This intentionally returns every matching row for the single visit. Any
    reduction happens later and only on the in-memory map display copy.
    """
    if not isinstance(mmsi, str) or not mmsi.strip():
        raise ValueError("A nonempty string MMSI is required")
    start, end = _utc_datetime(start_utc), _utc_datetime(end_utc)
    if start >= end:
        raise ValueError("AIS query start must precede end")
    with utc_connection() as connection:
        frame = connection.execute(
            "SELECT mmsi, timestamp_utc, lon, lat, sog_knots, cog_deg, nav_status "
            "FROM read_parquet(?) WHERE mmsi = ? AND timestamp_utc >= ? "
            "AND timestamp_utc < ? ORDER BY timestamp_utc",
            [str(SOURCES["ais"]), mmsi, start, end]).fetchdf()
    if not frame.empty:
        times = pd.to_datetime(frame.timestamp_utc, utc=True)
        if not frame.mmsi.astype(str).eq(mmsi).all() or (times < pd.Timestamp(start)).any() or (times >= pd.Timestamp(end)).any():
            raise AssertionError("AIS filter result escaped its MMSI/time bounds")
    return frame


def query_ais(mmsi: str, start_utc: Any, end_utc: Any, limit: int = 5000) -> pd.DataFrame:
    """Query only one MMSI within [start_utc, end_utc); never fetch all AIS."""
    if not isinstance(mmsi, str) or not mmsi.strip():
        raise ValueError("A nonempty string MMSI is required")
    start, end = _utc_datetime(start_utc), _utc_datetime(end_utc)
    if start >= end:
        raise ValueError("AIS query start must precede end")
    if not 1 <= limit <= 5000:
        raise ValueError("AIS query limit must be 1–5000")
    query = (
        "SELECT mmsi, timestamp_utc, lon, lat, sog_knots, nav_status, cog_deg "
        "FROM read_parquet(?) WHERE mmsi = ? AND timestamp_utc >= ? "
        "AND timestamp_utc < ? ORDER BY timestamp_utc LIMIT ?")
    with utc_connection() as connection:
        frame = connection.execute(query, [str(SOURCES["ais"]), mmsi, start, end, limit]).fetchdf()
    if not frame.empty:
        times = pd.to_datetime(frame.timestamp_utc, utc=True)
        if not frame.mmsi.astype(str).eq(mmsi).all() or (times < pd.Timestamp(start)).any() or (times >= pd.Timestamp(end)).any():
            raise AssertionError("AIS filter result escaped its MMSI/time bounds")
    return frame


def query_ais_count(mmsi: str, start_utc: Any, end_utc: Any) -> int:
    """Count a filtered vessel/time slice in DuckDB without materializing AIS."""
    start, end = _utc_datetime(start_utc), _utc_datetime(end_utc)
    if start >= end or not mmsi:
        raise ValueError("Valid MMSI and increasing UTC time bounds are required")
    with utc_connection() as connection:
        return int(connection.execute(
            "SELECT COUNT(*) FROM read_parquet(?) WHERE mmsi = ? "
            "AND timestamp_utc >= ? AND timestamp_utc < ?",
            [str(SOURCES["ais"]), mmsi, start, end]).fetchone()[0])


def validate_demo() -> Dict[str, Any]:
    sources = inspect_sources()
    links = validate_links()
    port_call_id = pick_sample_port_call()
    bundle = get_port_call_bundle(port_call_id)
    arr_time = _utc_datetime(bundle["port_call"]["arr_time_utc"])
    start, end = arr_time - timedelta(minutes=30), arr_time + timedelta(minutes=90)
    mmsi = str(bundle["port_call"]["mmsi"])
    trajectory = query_ais(mmsi, start, end, limit=5000)
    count = query_ais_count(mmsi, start, end)
    if count < len(trajectory) or count == 0:
        raise AssertionError("Sample Port Call AIS query returned no valid trajectory")
    return {
        "sources": sources, "links": links,
        "sample_port_call_id": port_call_id, "sample_mmsi": mmsi,
        "sample_label": bundle["label"].get("final_label"),
        "sample_chain": bundle["events"][["event_id", "event_type", "event_time_utc", "port_call_id"]].to_dict("records"),
        "ais_window_start_utc": start.isoformat(), "ais_window_end_utc": end.isoformat(),
        "ais_count_in_window": count, "ais_rows_returned": len(trajectory),
        "ais_first_time_utc": str(trajectory.timestamp_utc.min()),
        "ais_last_time_utc": str(trajectory.timestamp_utc.max()),
        "ais_sample": trajectory.head(5).to_dict("records"),
    }


if __name__ == "__main__":
    print(json.dumps(validate_demo(), ensure_ascii=False, indent=2, default=str))
