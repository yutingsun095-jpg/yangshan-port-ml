"""A-module historical AIS replay and online-safe Port Status presentation."""

from datetime import timedelta
from html import escape
from pathlib import Path
import sys

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from data_loader import (SOURCES, get_port_call_bundle, inspect_sources,
                         list_mmsis, list_port_calls, pick_sample_port_call,
                         query_visit_ais, validate_links)
from map_utils import build_port_call_map, current_spatial_region
from replay_utils import (chain_intervals, current_ais_at_or_before,
                          events_as_replayed, history_at_or_before,
                          port_status_at_or_before, utc, visit_query_window)
from b_runtime_adapter import (lead_time_bin_ascii, load_predictions,
                               prediction_bundle)
from c_interface import build_c_interface_record


STATUS_CARDS = [
    ("活跃船舶", "active_vessel_count", "最近AIS距当前快照不超过10分钟的船舶数量。"),
    ("外部接近区船舶", "approach_vessel_count", "当前位于研究定义External Approach区域的船舶数量。"),
    ("锚地区域船舶", "anchorage_present_count", "当前位于No.1 Anchorage官方边界内的船舶数量。"),
    ("确认锚泊等待", "anchorage_confirmed_waiting_count", "截至当前时刻已满足在线锚泊确认规则的船舶数量。"),
    ("Main泊位区确认活动", "berth_main_confirmed_occupied_count", "研究定义Main泊位区域内已满足在线稳定停留确认条件的船舶数量；不是官方泊位容量或泊位利用率。"),
    ("West泊位区确认活动", "berth_west_confirmed_occupied_count", "研究定义West泊位区域内已满足在线稳定停留确认条件的船舶数量；不是官方泊位容量或泊位利用率。"),
    ("近60分钟进港船舶", "unique_inbound_vessels_60m", "使用unique vessels，不是raw Gate crossing次数。"),
    ("近60分钟出港船舶", "unique_outbound_vessels_60m", "使用unique vessels，不是raw Gate crossing次数。"),
]

CASE_IDS = {
    "Indirect完整访问": "PC_202401_211886810_20240106T041143",
    "Direct完整访问": "PC_202401_538009831_20240101T235432",
    "缺少UNB/DEP": "PC_202401_414688000_20240101T021420",
}
LABEL_TEXT = {
    "direct": "直接靠泊 Direct",
    "indirect": "锚泊后靠泊 Indirect",
    "review": "待复核 Review",
    "not_eligible_no_ber": "未检测到正式BER",
}
EVENT_TEXT = {"ANC": "锚泊 ANC", "ARR": "到港 ARR", "BER": "靠泊 BER",
              "UNB": "离泊 UNB", "DEP": "离港 DEP"}


def utc_text(value):
    return "—" if value is None or pd.isna(value) else utc(value).strftime("%Y-%m-%d %H:%M:%S UTC")


def value_text(value, decimals=2):
    return "—" if value is None or pd.isna(value) else ("{:,." + str(decimals) + "f}").format(float(value))


def event_time_text(visible_events: pd.DataFrame, kind: str) -> str:
    rows = visible_events.loc[visible_events.event_type.eq(kind)].sort_values("event_time_utc")
    return "—" if rows.empty else utc_text(rows.iloc[0].event_time_utc)


def summary_event_display(formal_events: pd.DataFrame, visible_events: pd.DataFrame,
                          kind: str):
    """Distinguish a future formal event from an event absent from this visit."""
    visible = visible_events.loc[visible_events.event_type.eq(kind)].sort_values("event_time_utc")
    if not visible.empty:
        row = visible.iloc[0]
        note = "历史真值 · 当时尚未知" if row.get("replay_status") == "future" else None
        return utc_text(row.event_time_utc), note
    if formal_events.event_type.eq(kind).any():
        return "尚未发生 / 当前回放时刻尚不可见", None
    return "—", None


def interval_text(intervals, name: str) -> str:
    value = intervals.get(name)
    return "—" if value is None else "{:.2f} min".format(value)


def historical_stage(formal_events: pd.DataFrame, moment: pd.Timestamp) -> str:
    """Presentation-only stage from released historical event times."""
    by_type = {kind: formal_events.loc[formal_events.event_type.eq(kind)]
               for kind in EVENT_TEXT}
    for kind, stage in [("DEP", "已离港"), ("UNB", "已离泊"),
                        ("BER", "已靠泊"), ("ARR", "到港后进港／待靠泊")]:
        if not by_type[kind].empty and pd.to_datetime(
                by_type[kind].event_time_utc, utc=True).le(moment).any():
            return stage
    anc = by_type["ANC"]
    if not anc.empty:
        starts = pd.to_datetime(anc.event_time_utc, utc=True)
        ends = pd.to_datetime(anc.event_end_time_utc, utc=True)
        if ((starts <= moment) & (moment <= ends)).any():
            return "锚泊阶段"
        if (ends < moment).any():
            return "锚泊结束至到港"
    return "到港前航行"


def render_timeline(visible_events: pd.DataFrame) -> None:
    if visible_events.empty:
        st.info("此时刻尚未发生本次访问的正式事件。")
        return
    cards = []
    for event in visible_events.itertuples(index=False):
        future = event.replay_status == "future"
        title = escape(EVENT_TEXT[str(event.event_type)])
        when = escape(utc_text(event.event_time_utc))
        confidence = escape(str(event.confidence_level).capitalize())
        note = '<div class="a-timeline-note">历史真值 · 当时尚未知</div>' if future else ""
        cards.append('<div class="a-timeline-node{}"><div class="a-timeline-title">{}</div>'
                     '<div class="a-timeline-time">{}</div><div class="a-timeline-conf">{}</div>{}</div>'.format(
                         " future" if future else "", title, when, confidence, note))
    st.markdown("""
    <style>
    .a-timeline {display:flex;flex-wrap:wrap;gap:14px;align-items:stretch;margin:12px 0 20px}
    .a-timeline-node {min-width:158px;max-width:220px;flex:1;padding:15px 17px;
      border:1px solid #bed4e5;border-top:4px solid #2077a7;border-radius:9px;background:#f4f9fc}
    .a-timeline-node.future {border-color:#d5d8dc;border-top-color:#a3a8ae;
      background:#f3f4f5;color:#777e85}
    .a-timeline-title {font-weight:700;font-size:1rem}
    .a-timeline-time {font-size:.82rem;margin-top:9px;white-space:nowrap}
    .a-timeline-conf {font-size:.8rem;margin-top:5px}
    .a-timeline-note {font-size:.74rem;margin-top:8px}
    </style><div class="a-timeline">""" + "".join(cards) + "</div>", unsafe_allow_html=True)


def render_summary_cards(items) -> None:
    blocks = []
    for item in items:
        title, value = item[:2]
        note = item[2] if len(item) > 2 else None
        note_html = ('<div class="a-summary-note">{}</div>'.format(escape(str(note)))
                     if note else "")
        blocks.append('<div class="a-summary-card"><div class="a-summary-label">{}</div>'
                      '<div class="a-summary-value">{}</div>{}</div>'.format(
                          escape(str(title)), escape(str(value)), note_html))
    st.markdown("""
    <style>
    .a-summary-grid {display:grid;grid-template-columns:repeat(auto-fit,minmax(215px,1fr));
      gap:10px;margin:12px 0 8px}
    .a-summary-card {min-width:0;padding:11px 13px;border:1px solid #dde5eb;
      border-radius:8px;background:#fbfcfd}
    .a-summary-label {font-size:.82rem;color:#536779;margin-bottom:5px}
    .a-summary-value {font-size:.98rem;font-weight:600;color:#253443;
      overflow-wrap:anywhere;line-height:1.4}
    .a-summary-note {font-size:.74rem;color:#687787;margin-top:4px}
    </style><div class="a-summary-grid">""" + "".join(blocks) + "</div>", unsafe_allow_html=True)


def reveal_truth(value, event_time, replay_time, show_future_truth, *, suffix=""):
    """Reveal a historical target only once its corresponding event is reached."""
    if value is None or pd.isna(value):
        return "—", "unavailable/not applicable"
    reached = event_time is not None and not pd.isna(event_time) and utc(event_time) <= replay_time
    text = "{}{}".format(value, suffix)
    if reached:
        return text, "observed fact"
    if show_future_truth:
        return text, "历史真值 · 当时尚未知"
    return "—", "当前回放时刻尚未知"


def render_b_prediction_panel(*, call, label, replay_time, show_future_truth, predictions,
                              observed_chain, a_stage):
    """Present frozen B outputs while keeping historical truth visibly separate."""
    st.subheader("B模型预测 · 历史回放")
    st.warning("历史回放预测，不代表实时生产部署结果。B侧为模型预测；A侧正式事件和标签为历史事实，两者不会混写。")
    cols = st.columns(3)
    arr_time = utc(call["arr_time_utc"])
    ber_time = None if pd.isna(call.get("ber_time_utc")) else utc(call["ber_time_utc"])

    with cols[0]:
        st.markdown("#### VAT 到ARR")
        vat = predictions.get("vat")
        if vat is None:
            st.info("截至当前回放时刻，没有可用的预计算 VAT 预测。")
        else:
            st.metric("B预测 VAT", "{:.2f} min".format(float(vat["prediction"])))
            st.caption("预测生成：{}；数据截至：{}".format(
                utc_text(vat["prediction_time_utc"]), utc_text(vat["data_available_until_utc"])))
            st.caption("预测剩余时间区间：{}（由预测值划分，非未来真值）".format(
                lead_time_bin_ascii(vat["prediction"])))
            actual, actual_note = reveal_truth(
                "{:.2f} min".format(float(vat["lead_time_min"])), arr_time,
                replay_time, show_future_truth)
            st.write("A侧实际 VAT：**{}**".format(actual))
            st.caption(actual_note)
            if actual_note != "当前回放时刻尚未知":
                st.write("绝对误差：**{:.2f} min**".format(
                    abs(float(vat["prediction"]) - float(vat["lead_time_min"]))))
            st.caption("ARR event_id：{}；模型：{}；QC：{}".format(
                vat["event_id"], vat["model_version"], vat["qc_flag"]))

    with cols[1]:
        st.markdown("#### Direct / Indirect")
        direct = predictions.get("direct_indirect")
        is_high_trainable = (
            str(label.get("label_confidence")) == "high"
            and str(label.get("final_label")) in {"direct", "indirect"}
        )
        if direct is None:
            st.info("截至当前回放时刻，没有可用的预计算 Direct/Indirect 预测。")
        else:
            st.metric("B预测类别", str(direct["predicted_class"]))
            st.write("Indirect 概率：**{:.3f}**（阈值 {:.2f}）".format(
                float(direct["prediction"]), float(direct["decision_threshold"])))
            st.caption("预测生成：{}；数据截至：{}；概率未校准。".format(
                utc_text(direct["prediction_time_utc"]), utc_text(direct["data_available_until_utc"])))
            if is_high_trainable:
                truth, truth_note = reveal_truth(
                    LABEL_TEXT.get(str(label["final_label"]), str(label["final_label"])),
                    ber_time, replay_time, show_future_truth)
                st.write("A侧正式标签：**{}**".format(truth))
                st.caption(truth_note)
                if truth_note != "当前回放时刻尚未知":
                    expected = "Direct" if str(label["final_label"]) == "direct" else "Indirect"
                    st.write("预测是否正确：**{}**".format("是" if direct["predicted_class"] == expected else "否"))
            else:
                st.write("A侧正式标签：**不适用**")
                st.caption("仅对A侧正式高置信、可训练的 Direct/Indirect 样本评价；Review 与 not_eligible_no_ber 不编码为 Direct。")
            st.caption("适用性：空间特征贡献较强，仅作为洋山港当前研究场景的历史回放展示。")

    with cols[2]:
        st.markdown("#### ARR 到 BER")
        wait = predictions.get("arr_to_ber")
        is_high_high = (
            str(call.get("arr_confidence_level")) == "high"
            and str(call.get("ber_confidence_level")) == "high"
        )
        if wait is None:
            message = "该访问不属于 ARR=high 且 BER=high 的326条主模型适用样本。"
            if is_high_high:
                message = "截至当前回放时刻，没有可用的预计算 ARR→BER 预测。"
            st.info(message)
        else:
            st.metric("B预测 ARR→BER", "{:.2f} min".format(float(wait["prediction"])))
            st.caption("预测生成：{}；数据截至：{}".format(
                utc_text(wait["prediction_time_utc"]), utc_text(wait["data_available_until_utc"])))
            actual, actual_note = reveal_truth(
                "{:.2f} min".format(float(call["arr_to_ber_min"])), ber_time,
                replay_time, show_future_truth)
            st.write("A侧实际 ARR→BER：**{}**".format(actual))
            st.caption(actual_note)
            if actual_note != "当前回放时刻尚未知":
                st.write("绝对误差：**{:.2f} min**".format(
                    abs(float(wait["prediction"]) - float(call["arr_to_ber_min"]))))
            arr_conf, arr_conf_note = reveal_truth(call["arr_confidence_level"], arr_time,
                                                    replay_time, show_future_truth)
            ber_conf, ber_conf_note = reveal_truth(call["ber_confidence_level"], ber_time,
                                                    replay_time, show_future_truth)
            st.caption("ARR置信度：{}（{}）；BER置信度：{}（{}）".format(
                arr_conf, arr_conf_note, ber_conf, ber_conf_note))
            st.caption("模型：{}；长尾不确定性未量化；最终模型不含 Port Status 特征。".format(
                wait["model_version"]))

    c_record = build_c_interface_record(
        port_call=call,
        replay_time_utc=replay_time,
        current_a_status=a_stage,
        observed_events=observed_chain,
        predictions=predictions,
    )
    import json
    st.download_button(
        "导出给C的当前回放标准接口 JSON",
        data=json.dumps(c_record, ensure_ascii=False, indent=2),
        file_name="c_interface_{}_{}.json".format(
            call["port_call_id"], replay_time.strftime("%Y%m%dT%H%M%SZ")),
        mime="application/json",
        help="仅含截至当前回放时刻的A侧事实和已可用B侧预测；不含未来历史真值。",
    )


st.set_page_config(page_title="洋山港船舶访问事件链与动态状态回放", layout="wide")
st.title("洋山港船舶访问事件链与动态状态回放")
st.caption("基于AIS重构船舶进港、锚泊、靠泊、离泊与离港过程，并同步展示历史时刻的洋山港动态运行状态。")
st.caption("这艘船和港口在历史上发生了什么？")


@st.cache_data(show_spinner=False)
def source_summary():
    return inspect_sources(), validate_links()


@st.cache_data(show_spinner=False)
def mmsi_options():
    return list_mmsis()


@st.cache_data(show_spinner=False)
def port_calls_for(mmsi: str):
    return list_port_calls(mmsi)


@st.cache_data(show_spinner=False)
def selected_bundle(port_call_id: str):
    return get_port_call_bundle(port_call_id)


@st.cache_data(show_spinner=False, max_entries=8)
def selected_ais(mmsi: str, start: pd.Timestamp, end: pd.Timestamp):
    return query_visit_ais(mmsi, start, end)


@st.cache_data(show_spinner=False, max_entries=64)
def selected_status(replay_time_utc: pd.Timestamp):
    return port_status_at_or_before(SOURCES["port_status"], replay_time_utc)


@st.cache_data(show_spinner=False)
def frozen_b_predictions():
    return (load_predictions("vat"), load_predictions("direct_indirect"),
            load_predictions("arr_to_ber"))


try:
    source_summary()  # First-round read-only input and key-link validation.
    default_id = pick_sample_port_call()
    default_mmsi = str(selected_bundle(default_id)["port_call"]["mmsi"])
    mmsis = mmsi_options()
    st.sidebar.header("选择访问")
    case_choice = st.sidebar.selectbox("典型案例", ["自由选择"] + list(CASE_IDS))
    if st.session_state.get("_last_case_choice") != case_choice:
        if case_choice in CASE_IDS:
            chosen_id = CASE_IDS[case_choice]
            st.session_state["demo_mmsi"] = chosen_id.split("_")[2]
            st.session_state["demo_port_call"] = chosen_id
        st.session_state["_last_case_choice"] = case_choice
    if st.session_state.get("demo_mmsi") not in mmsis:
        st.session_state["demo_mmsi"] = default_mmsi if default_mmsi in mmsis else mmsis[0]
    mmsi = st.sidebar.selectbox("MMSI", mmsis, key="demo_mmsi")
    visits = port_calls_for(mmsi)
    if visits.empty:
        st.warning("该MMSI没有已发布的Port Call")
        st.stop()
    visit_ids = visits.port_call_id.astype(str).tolist()
    visit_labels = {
        str(row.port_call_id): "{} · ARR {}".format(
            row.port_call_id, utc(row.arr_time_utc).strftime("%Y-%m-%d %H:%M UTC"))
        for row in visits.itertuples(index=False)
    }
    if st.session_state.get("demo_port_call") not in visit_ids:
        st.session_state["demo_port_call"] = default_id if default_id in visit_ids else visit_ids[0]
    port_call_id = st.sidebar.selectbox(
        "Port Call", visit_ids, key="demo_port_call",
        format_func=lambda item: visit_labels[item])
    bundle = selected_bundle(port_call_id)
    call, label, events = bundle["port_call"], bundle["label"], bundle["events"]
    if str(call["mmsi"]) != mmsi or not events.port_call_id.eq(port_call_id).all():
        raise ValueError("MMSI / Port Call / formal event association mismatch")
    start, end, start_rule, end_rule = visit_query_window(events)
    with st.spinner("按MMSI和UTC访问窗口查询正式AIS…"):
        ais = selected_ais(mmsi, start, end)

    slider_start = start.tz_localize(None).to_pydatetime()
    slider_end = end.tz_localize(None).to_pydatetime()
    arr = utc(call["arr_time_utc"])
    slider_default = min(max(arr, start), end).tz_localize(None).to_pydatetime()
    st.subheader("时间回放")
    replay_input = st.slider("replay_time_utc（UTC；10 min步长）",
                             min_value=slider_start, max_value=slider_end,
                             value=slider_default, step=timedelta(minutes=10),
                             format="YYYY-MM-DD HH:mm:ss", key="replay_" + port_call_id)
    replay_time = pd.Timestamp(replay_input).tz_localize("UTC")
    show_future_truth = st.checkbox("显示完整历史真值事件链", value=False)
    vat_predictions, direct_predictions, wait_predictions = frozen_b_predictions()
    predictions = prediction_bundle(
        call, replay_time, vat_predictions, direct_predictions, wait_predictions)
    history = history_at_or_before(ais, replay_time)
    current, ais_age_min = current_ais_at_or_before(history, replay_time)
    snapshot, snapshot_age_min = selected_status(replay_time)
    with st.spinner("绘制截至回放时刻的轨迹及事件…"):
        map_view, map_info = build_port_call_map(
            SOURCES["geofence"], history, events, replay_time_utc=replay_time,
            include_future_truth=show_future_truth)
    chain = events_as_replayed(events, replay_time, include_future_truth=show_future_truth)
    observed_chain = events_as_replayed(events, replay_time, include_future_truth=False)
    if (map_info["max_ais_time_utc"] is not None and
            map_info["max_ais_time_utc"] > replay_time):
        raise AssertionError("Map used future AIS")
    if snapshot is not None and utc(snapshot["snapshot_time_utc"]) > replay_time:
        raise AssertionError("Port Status used a future snapshot")
    if current is not None and utc(current["timestamp_utc"]) > replay_time:
        raise AssertionError("Current position used future AIS")
except Exception as exc:
    st.error("只读历史回放加载失败：{}".format(exc))
    st.stop()

st.caption("查询窗口：{} → {}（{}；{}）；本次回放：{}。".format(
    utc_text(start), utc_text(end), start_rule, end_rule, utc_text(replay_time)))
st.subheader("洋山港动态运行状态 · 在线安全历史快照")
cards = st.columns(4)
for index, (title, field, help_text) in enumerate(STATUS_CARDS):
    cards[index % 4].metric(title, "—" if snapshot is None else str(int(snapshot[field])),
                            help=help_text)
if snapshot is None:
    st.warning("当前回放时间之前没有Port Status快照；不使用未来快照填补。")
else:
    st.caption("Port Status snapshot: {}　|　Replay time: {}　|　Snapshot age: {:.2f} min".format(
        utc_text(snapshot["snapshot_time_utc"]), utc_text(replay_time), snapshot_age_min))
    if snapshot_age_min >= 10:
        st.error("Port Status快照超过正常10分钟粒度，需复核数据覆盖。")
    with st.expander("Gate raw crossing诊断（非船舶数量）"):
        st.write("observed_inbound_gate_crossings_60m: {}；observed_outbound_gate_crossings_60m: {}".format(
            snapshot["observed_inbound_gate_crossings_60m"],
            snapshot["observed_outbound_gate_crossings_60m"]))
        st.caption("Gate observed crossings，可能包含同船重复穿越，不等于船舶数量。")

render_b_prediction_panel(
    call=call, label=label, replay_time=replay_time,
    show_future_truth=show_future_truth, predictions=predictions,
    observed_chain=observed_chain, a_stage=historical_stage(events, replay_time))
st.subheader("当前船舶状态")
st.caption("Port Call ID：{}　|　MMSI：{}　|　Replay time UTC：{}".format(
    port_call_id, mmsi, utc_text(replay_time)))
if current is None:
    st.info("回放时刻以前在本访问查询窗口内尚无AIS；不伪造当前位置。")
else:
    region = current_spatial_region(SOURCES["geofence"], current["lon"], current["lat"])
    st.write("当前AIS时间：**{}**　｜　AIS age：**{} min**　｜　当前空间区域：**{}**".format(
        utc_text(current["timestamp_utc"]), value_text(ais_age_min), region))
    vessel_cols = st.columns(4)
    for index, (title, value) in enumerate([
        ("当前经度", value_text(current["lon"], 6)),
        ("当前纬度", value_text(current["lat"], 6)),
        ("当前SOG kn", value_text(current["sog_knots"])),
        ("当前COG deg", value_text(current["cog_deg"])),
    ]):
        vessel_cols[index % 4].metric(title, value)
    if ais_age_min > 10:
        st.warning("AIS状态可能过期；仅显示最后一个真实AIS点，不外推当前位置。")

st.info("当前历史阶段：{}。仅依据已发布历史事件的时间进行展示，不重新识别事件或预测。".format(
    historical_stage(events, replay_time)))
st.subheader("截至回放时刻的空间轨迹与事件")
components.html(map_view.get_root().render(), height=670, scrolling=False)
st.caption("查询窗口AIS共{}点；截至回放时刻{}点；地图显示{}点（仅显示副本抽稀）。BER不伪造单点坐标。".format(
    len(ais), len(history), map_info["ais_display_count"]))
st.caption("确定版GeoJSON已加载{}个对象。".format(len(map_info["geojson_objects"])))

st.subheader("截至当前回放时刻的访问摘要")
st.caption("离线访问标签为最终历史真值；未来事件时间默认隐藏，仅在开启完整历史真值后显示。")
summary_intervals = chain_intervals(chain)
label_raw = str(label["final_label"])
summary_items = [
    ("MMSI", mmsi), ("Port Call ID", port_call_id),
    ("最终访问类型", LABEL_TEXT.get(label_raw, label_raw)),
    ("标签置信度", str(label["label_confidence"])),
]
st.caption("原始标签值：{}".format(label_raw))
linked_anc = chain.loc[chain.event_type.eq("ANC")]
if not linked_anc.empty:
    anc_text = "是（{}次）".format(len(linked_anc))
elif events.event_type.eq("ANC").any() and not show_future_truth:
    anc_text = "—"
else:
    anc_text = "否"
summary_items.append(("关联锚泊 ANC", anc_text))
summary_items.append(("到港 ARR", event_time_text(chain, "ARR")))
for title, kind in [("靠泊 BER", "BER"), ("离泊 UNB", "UNB"), ("离港 DEP", "DEP")]:
    value, note = summary_event_display(events, chain, kind)
    summary_items.append((title, value, note))
summary_items.extend((title, interval_text(summary_intervals, key)) for title, key in [
    ("锚泊结束至到港", "ANC end→ARR min"),
    ("到港至靠泊历时", "ARR→BER min"),
    ("靠泊至离泊历时", "BER→UNB min"),
    ("离泊至离港历时", "UNB→DEP min"),
])
render_summary_cards(summary_items)

st.subheader("船舶访问事件时间轴")
st.caption("默认仅显示截至当前回放时刻已发生的正式事件；开启“显示完整历史真值事件链”后，可查看后续历史事件，其中未来事件仅作为离线历史真值展示。")
render_timeline(chain)
with st.expander("详细事件数据"):
    if chain.empty:
        st.write("—")
    else:
        table = chain[["event_id", "event_type", "event_time_utc", "event_end_time_utc",
                       "confidence_level", "qc_flag", "replay_status", "replay_note"]].copy()
        def shade_future(row):
            style = "color: #9ca3af; background-color: #f3f4f6" if row["replay_status"] == "future" else ""
            return [style] * len(row)
        st.dataframe(table.style.apply(shade_future, axis=1),
                     use_container_width=True, hide_index=True)
if show_future_truth:
    st.caption("灰色事件为历史真值 · 当时尚未知（Ground truth future event — not available as online input）。")
with st.expander("方法与数据口径说明"):
    st.markdown("""
1. 到港 ARR：船舶由海侧向港侧有效穿越研究定义Arrival Gate。
2. 锚泊 ANC：基于No.1官方锚地区域及低速持续停留规则识别。
3. 靠泊 BER：基于Main/West研究泊位区域的稳定低速停留识别；不伪造单点坐标。
4. 离泊 UNB：最终持续离开联合泊位区域。
5. 离港 DEP：沿离港方向穿越与ARR相同的Arrival Gate。
6. Port Status每10分钟构建一次，只使用当时及之前AIS信息。
7. No.1 Anchorage及Pilot Points来源于已有官方／公开空间定义；Arrival Gate、External Approach、Main/West为本研究空间定义。
8. Main/West不是官方泊位边界，也不是官方泊位容量或泊位利用率。

ARR/ANC/BER/UNB/DEP是离线确定版历史事件真值；Port Status是按当时可获得AIS构建的在线安全状态。ANC/BER等事件有确认窗口，两者的时间可能不同。地图AIS、当前AIS、Port Status快照均不得晚于回放时刻。""")
