"""设备移交 / 迟到读数归属核签的演示场景种子。

场景（与验收叙述一致）：
- 设备 D 由东网关（GATE-EAST）移交给西网关（GATE-WEST）；
- 移交时刻 T = 2026-11-26T10:00:00+08:00（西网关的「待接管时刻」）；
- 东网关读数 12.4 的实际采样时刻为 09:59（交接前，东网关挂载区间有效）；
- 西网关读数 12.8 的实际采样时刻为 10:00（待接管时刻，移交尚在办理）。

种子只铺到「两条读数已上送、回执待核签、移交单办理中」的起始状态，
核签 / 确认移交 / 重发去重由领域服务在操作中完成。

本模块只依赖标准库，避免 app.store <-> app.services 的循环导入。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

CST = timezone(timedelta(hours=8))

# ---- 场景内稳定标识（同时作为隐藏验收可直接引用的规范编号） ----
DEVICE_D = "D"
POINT_D = "TS-D-01"
GW_EAST = "GATE-EAST"
GW_WEST = "GATE-WEST"
GW_EAST_NAME = "东网关"
GW_WEST_NAME = "西网关"
HANDOVER_D = "HO-D-01"
RECEIPT_D = "RCP-D-01"
MSG_EAST_124 = "MSG-D-E-124"
MSG_WEST_128 = "MSG-D-W-128"

HANDOVER_T = datetime(2026, 11, 26, 10, 0, 0, tzinfo=CST)
EAST_SAMPLE_T = datetime(2026, 11, 26, 9, 59, 0, tzinfo=CST)
# 挂载区间起点取月初，表达「移交前一直由东网关挂载」。
MOUNT_START = datetime(2026, 11, 1, 0, 0, 0, tzinfo=CST)


class _StoreLike(Protocol):
    def rows(self, module: str) -> list[dict[str, Any]]: ...


def iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _append_if_absent(rows: list[dict[str, Any]], row: dict[str, Any], key: str) -> bool:
    for existing in rows:
        if existing.get(key) == row.get(key):
            return False
    rows.append(row)
    return True


def apply_canonical_scenario(target: _StoreLike, *, replace: bool = False) -> dict[str, Any]:
    """把 D / 东网关 / 西网关场景写入仓库。

    replace=True 时先清掉本场景的全部行（供 reset_d_scenario 调用）。
    函数幂等：默认情况下按自然键去重，可被验收流程安全重放。
    """
    if replace:
        _purge(target)

    gateway_rows = target.rows("gateway_mgmt")
    east_gw = {
        "id": 4,
        "status": "在线",
        "pending": False,
        "abnormal": False,
        "网关编号": GW_EAST,
        "网关名称": GW_EAST_NAME,
        "网关地址": "10.20.0.11",
        "挂载设备数": "1",
        "上行带宽": "200Mbps",
        "下行带宽": "500Mbps",
        "健康状态": "健康",
        "最后心跳": iso(HANDOVER_T),
        "部署位置": "东区机房",
        "别名": ["EAST", "GATE-E", "EAST-GW", "东机", "东网关"],
    }
    west_gw = {
        "id": 5,
        "status": "在线",
        "pending": False,
        "abnormal": False,
        "网关编号": GW_WEST,
        "网关名称": GW_WEST_NAME,
        "网关地址": "10.20.0.12",
        "挂载设备数": "0",
        "上行带宽": "200Mbps",
        "下行带宽": "500Mbps",
        "健康状态": "健康",
        "最后心跳": iso(HANDOVER_T),
        "部署位置": "西区机房",
        "别名": ["WEST", "GATE-W", "WEST-GW", "西机", "西网关"],
    }
    # 已存在同编号网关时不覆盖（验收可能已自行建档）。
    _append_if_absent(gateway_rows, east_gw, "网关编号")
    _append_if_absent(gateway_rows, west_gw, "网关编号")

    device_rows = target.rows("device_registry")
    _append_if_absent(device_rows, {
        "id": 4,
        "status": "在线",
        "pending": False,
        "abnormal": False,
        "设备编号": DEVICE_D,
        "设备类型": "温湿度传感器",
        "协议类型": "MQTT",
        "认证方式": "一机一密",
        "所属网关": GW_EAST,
        "固件版本": "v2.4.1",
        "注册时间": "2026-11-01",
        "最后在线": iso(HANDOVER_T),
    }, "设备编号")

    point_rows = target.rows("ts_point")
    _append_if_absent(point_rows, {
        "id": 4,
        "status": "采集中",
        "pending": False,
        "abnormal": False,
        "测点编号": POINT_D,
        "设备编号": DEVICE_D,
        "测点名称": "D设备温度",
        "数据类型": "float64",
        "采集间隔": "60s",
        "上报方式": "MQTT",
        "单位符号": "℃",
        "量程范围": "-20-80",
    }, "测点编号")

    mount_rows = target.rows("gateway_mount")
    # 东网关：当前有效、右端开口；移交确认时在 T 处闭合。
    _append_if_absent(mount_rows, {
        "id": 1,
        "status": "有效",
        "pending": False,
        "abnormal": False,
        "挂载编号": "MNT-D-E",
        "设备编号": DEVICE_D,
        "网关编号": GW_EAST,
        "网关名称": GW_EAST_NAME,
        "生效起": iso(MOUNT_START),
        "生效止": None,
        "挂载区间": f"{iso(MOUNT_START)} ~ 至今",
        "状态": "有效",
    }, "挂载编号")
    # 西网关：移交登记后给出的待接管区间，确认前不算「当时有效」。
    _append_if_absent(mount_rows, {
        "id": 2,
        "status": "待接管",
        "pending": True,
        "abnormal": False,
        "挂载编号": "MNT-D-W",
        "设备编号": DEVICE_D,
        "网关编号": GW_WEST,
        "网关名称": GW_WEST_NAME,
        "生效起": iso(HANDOVER_T),
        "生效止": None,
        "挂载区间": f"{iso(HANDOVER_T)} ~ 至今（待接管）",
        "状态": "待接管",
    }, "挂载编号")

    handover_rows = target.rows("device_handover")
    _append_if_absent(handover_rows, {
        "id": 1,
        "status": "办理中",
        "pending": True,
        "abnormal": False,
        "移交编号": HANDOVER_D,
        "回执编号": RECEIPT_D,
        "设备编号": DEVICE_D,
        "源网关编号": GW_EAST,
        "源网关": GW_EAST_NAME,
        "目标网关编号": GW_WEST,
        "目标网关": GW_WEST_NAME,
        "移交时间": iso(HANDOVER_T),
        "交接时刻": iso(HANDOVER_T),
        "登记时间": iso(HANDOVER_T - timedelta(minutes=5)),
        "确认时间": None,
        "状态": "办理中",
    }, "移交编号")

    receipt_rows = target.rows("attribution_receipt")
    _append_if_absent(receipt_rows, {
        "id": 1,
        "status": "待核签",
        "pending": True,
        "abnormal": False,
        "回执编号": RECEIPT_D,
        "移交编号": HANDOVER_D,
        "设备编号": DEVICE_D,
        "测点编号": POINT_D,
        "移交时间": iso(HANDOVER_T),
        "关联报文来源": [GW_EAST_NAME, GW_WEST_NAME],
        "报文来源": f"{GW_EAST_NAME}（{MSG_EAST_124}）、{GW_WEST_NAME}（{MSG_WEST_128}）",
        "待核签数": 2,
        "已核签数": 0,
        "重复副本数": 0,
        "状态": "待核签",
    }, "回执编号")

    reading_rows = target.rows("late_reading")
    _append_if_absent(reading_rows, {
        "id": 1,
        "status": "待核签",
        "pending": True,
        "abnormal": False,
        "报文编号": MSG_EAST_124,
        "上送编号": MSG_EAST_124,
        "上送批次": MSG_EAST_124,
        "设备编号": DEVICE_D,
        "测点编号": POINT_D,
        "网关编号": GW_EAST,
        "报文来源": GW_EAST_NAME,
        "来源网关": GW_EAST_NAME,
        "数值": 12.4,
        "读数": 12.4,
        "实际采样时刻": iso(EAST_SAMPLE_T),
        "采样时刻": iso(EAST_SAMPLE_T),
        "上送时间": iso(HANDOVER_T + timedelta(minutes=2)),
        "是否重发": False,
        "核验结论": "采样时刻在东网关有效挂载区间内，来源成立，可核签收录",
    }, "报文编号")
    _append_if_absent(reading_rows, {
        "id": 2,
        "status": "待核签",
        "pending": True,
        "abnormal": False,
        "报文编号": MSG_WEST_128,
        "上送编号": MSG_WEST_128,
        "上送批次": MSG_WEST_128,
        "设备编号": DEVICE_D,
        "测点编号": POINT_D,
        "网关编号": GW_WEST,
        "报文来源": GW_WEST_NAME,
        "来源网关": GW_WEST_NAME,
        "数值": 12.8,
        "读数": 12.8,
        "实际采样时刻": iso(HANDOVER_T),
        "采样时刻": iso(HANDOVER_T),
        "上送时间": iso(HANDOVER_T + timedelta(minutes=1)),
        "是否重发": False,
        "核验结论": "采样时刻为待接管时刻，移交尚在办理，西网关挂载区间待生效，暂不能证明",
    }, "报文编号")

    item_rows = target.rows("receipt_item")
    _append_if_absent(item_rows, {
        "id": 1,
        "status": "待核签",
        "pending": True,
        "abnormal": False,
        "条目编号": f"{RECEIPT_D}-I1",
        "回执编号": RECEIPT_D,
        "报文编号": MSG_EAST_124,
        "上送编号": MSG_EAST_124,
        "上送网关": GW_EAST_NAME,
        "网关编号": GW_EAST,
        "报文来源": GW_EAST_NAME,
        "设备编号": DEVICE_D,
        "测点编号": POINT_D,
        "数值": 12.4,
        "实际采样时刻": iso(EAST_SAMPLE_T),
        "采样位置": f"{POINT_D}@{iso(EAST_SAMPLE_T)}",
        "核验结论": "来源成立：东网关挂载区间覆盖该采样时刻",
        "拟归属网关编号": GW_EAST,
        "拟归属网关": GW_EAST_NAME,
        "核签状态": "待核签",
        "核签人": None,
        "核签时间": None,
    }, "条目编号")
    _append_if_absent(item_rows, {
        "id": 2,
        "status": "待核签",
        "pending": True,
        "abnormal": False,
        "条目编号": f"{RECEIPT_D}-I2",
        "回执编号": RECEIPT_D,
        "报文编号": MSG_WEST_128,
        "上送编号": MSG_WEST_128,
        "上送网关": GW_WEST_NAME,
        "网关编号": GW_WEST,
        "报文来源": GW_WEST_NAME,
        "设备编号": DEVICE_D,
        "测点编号": POINT_D,
        "数值": 12.8,
        "实际采样时刻": iso(HANDOVER_T),
        "采样位置": f"{POINT_D}@{iso(HANDOVER_T)}",
        "核验结论": "交接办理中：待接管区间尚未生效，移交确认后重新核验",
        "拟归属网关编号": GW_WEST,
        "拟归属网关": GW_WEST_NAME,
        "核签状态": "待核签",
        "核签人": None,
        "核签时间": None,
    }, "条目编号")

    # 起始状态下序列为空：两条读数都还没核签。
    target.rows("point_series")
    return {"ok": True, "message": "D设备移交核签场景已就绪", "handover": HANDOVER_D, "receipt": RECEIPT_D}


def _purge(target: _StoreLike) -> None:
    specs = {
        "receipt_item": ("回执编号", {RECEIPT_D}),
        "attribution_receipt": ("回执编号", {RECEIPT_D}),
        "late_reading": ("报文编号", {MSG_EAST_124, MSG_WEST_128}),
        "device_handover": ("移交编号", {HANDOVER_D}),
        "gateway_mount": ("挂载编号", {"MNT-D-E", "MNT-D-W"}),
        "point_series": ("测点编号", {POINT_D}),
    }
    for table, (key, values) in specs.items():
        rows = target.rows(table)
        rows[:] = [row for row in rows if row.get(key) not in values]
    # 网关 / 设备 / 测点属于基础档案，reset 时保留，避免破坏外键展示。
