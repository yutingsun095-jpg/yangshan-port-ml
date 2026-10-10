# 洋山港 AIS 事件链与动态港口状态历史回放 Demo（全量公网版）

这是已验收本地 A 模块 Streamlit Demo 的全量公网部署副本。页面保留全部 562 艘船、712 条正式 Port Call、五类正式事件、Direct/Indirect 标签、10 分钟 Port Status、7 个确定版空间对象，以及每条访问实际使用的 AIS 回放轨迹。

公网版没有重新设计界面或改变算法。`app.py`、`map_utils.py` 和 `replay_utils.py` 与本地正式版保持一致；仅将数据路径改为应用目录内的相对路径，并把 1.78 GB Stage2 AIS 按本地 Demo 已验证的 Port Call 查询窗口裁剪为轻量 Parquet。

## 数据范围

- Port Call：712 条
- 唯一 MMSI：562 艘
- 正式事件：ARR / ANC / BER / UNB / DEP，共 1,600 条
- Port Status：4,464 个 10 分钟快照
- GeoJSON：7 个确定版空间对象
- 公网 AIS：2,068,585 条，7 个页面实际使用字段
- AIS 字段：`mmsi`、`timestamp_utc`、`lon`、`lat`、`sog_knots`、`cog_deg`、`nav_status`

AIS窗口与本地版 `visit_query_window` 完全一致：若访问关联正式 ANC，则从首个 ANC 开始前30分钟起；否则从 ARR 前6小时起。结束边界依次为 DEP 后30分钟、UNB 后90分钟、BER 后6小时或 ARR 后3小时。相同 MMSI 的重叠窗口取并集，查询时仍按当前 MMSI 与访问时间范围读取，因此不会混入其他访问的无关数据。

## 目录

```text
app.py
data_loader.py
map_utils.py
replay_utils.py
requirements.txt
README.md
.gitignore
data/
  data_manifest.json
  demo_ais_all_port_calls_000.parquet
  stage4_yangshan_port_calls_final_202401确定版.parquet
  stage4_yangshan_direct_indirect_labels_202401确定版.parquet
  stage5_yangshan_port_status_10min_202401确定版.parquet
  stage6_yangshan_vessel_events_202401确定版.parquet
  yangshan_geofence_candidate_v2确定版.geojson
```

所有运行时路径均相对于本目录，不依赖开发电脑盘符。AIS仍由 DuckDB 按 `MMSI + UTC时间范围` 查询，不会一次载入全部轨迹到 Pandas。

## 本地运行

建议使用 Python 3.10 或 3.11：

```bash
python -m venv .venv
python -m pip install -r requirements.txt
streamlit run app.py
```

## Streamlit Community Cloud

1. 将本目录内容提交到 GitHub 仓库；
2. 在 Streamlit Community Cloud 新建应用；
3. Main file path 选择本目录中的 `app.py`；
4. 部署后检查地图底图的公网瓦片访问。

数据文件均为普通 Git 文件，单文件低于 100 MB，不要求 Git LFS。OpenStreetMap 底图瓦片需要部署环境可联网；研究几何、AIS、事件、标签及 Port Status 均来自仓库内只读文件。

## 一致性与数据保护

构建后已对20个不同MMSI的随机/分层 Port Call进行原版—公网版逐项对照，覆盖 Direct、Indirect、Main、West、无BER及无UNB/DEP访问。在各访问完整查询窗口及多个回放时刻，AIS记录、当前船位、当前船舶状态、五类事件、Port Status、地图范围、访问摘要和时间线的底层输入与结果一致。

本目录是独立复制件；运行时不会写回正式 AIS、GeoJSON、事件、Port Call、标签或 Port Status 文件。
