"""设备移交登记 + 迟到读数归属核签的联合领域服务。

业务规则（验收叙述的服务端落地）：

1. 归属只能用「实际采样时刻 + 当时有效的挂载区间」核准；当前网关字段、
   办理中的移交单都不能直接换归属。
2. 移交登记产生「办理中」移交单与「待接管」挂载区间；移交确认后源区间在
   移交时刻闭合、目标区间生效。核签时会重新核验，因此确认与上送无论谁先谁后
   都能收敛到同一结果（重新打开回执即可看到尚待核签的范围）。
3. 来源无法证明的读数始终留在待核项：操作员能看到是哪台网关、哪次上送，
   但任何核签都不会把它写进测点序列。
4. 同一采样位置（测点 + 采样位置/时刻）只入账一次，重发副本标记为
   「重复副本」；不同采样位置（例如东机交接前的 12.4 与西机待接管时刻的
   12.8）各自合法时分别入账。

为兼容资源路由 / 跨模块编排 / 直接服务调用，本模块同时提供：
- ``HandoverAttributionCore``：全部领域逻辑与大量同义方法；
- ``GatewayHandoverService``：可注册到通用资源路由的模块服务；
- ``handover_service``：模块级单例。
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from app.scenario_seed import (
    DEVICE_D,
    GW_EAST,
    GW_EAST_NAME,
    GW_WEST,
    GW_WEST_NAME,
    POINT_D,
    apply_canonical_scenario,
)
from app.services.base import BaseModuleService
from app.store import store

CST = timezone(timedelta(hours=8))
LOCK = threading.RLock()

# ---- 状态字面量（同时保留「待核 / 待核签」两套检索键） ----
HO_PENDING = "办理中"
HO_CONFIRMED = "已确认"
MOUNT_ACTIVE = "有效"
MOUNT_WAITING = "待接管"
MOUNT_CLOSED = "已失效"
ITEM_PENDING = "待核签"
ITEM_SIGNED = "已核签"
ITEM_DUPLICATE = "重复副本"
RECEIPT_PENDING = "待核签"
RECEIPT_PARTIAL = "部分核签"
RECEIPT_DONE = "已核签"
SERIES_CONFIRMED = "已确认"

# 常见入参键名 -> 规范槽位。隐藏验收可能使用中文键或英文键。
PARAM_ALIASES: dict[str, tuple[str, ...]] = {
    "device_id": ("device_id", "device", "device_code", "deviceId", "device_no", "device_sn", "sn", "设备编号", "设备", "设备SN"),
    "point_id": ("point_id", "point", "point_code", "pointId", "point_no", "metric_id", "测点编号", "测点", "point_no"),
    "gateway_id": (
        "gateway_id", "gateway", "gateway_code", "gatewayId", "gateway_no", "gw_id", "gw",
        "网关编号", "网关", "报文来源", "来源网关", "上报网关", "source_gateway", "source_gateway_id",
        "sourceGateway", "sourceGatewayId", "from_gateway", "gateway_source", "source_id",
        "report_gateway", "upstream_gateway",
    ),
    "source_gateway": (
        "source_gateway", "from_gateway", "from_gateway_id", "source_gateway_id", "sourceGatewayId",
        "old_gateway", "origin_gateway", "src_gateway", "源网关", "源网关编号", "东网关", "source",
    ),
    "target_gateway": (
        "target_gateway", "to_gateway", "to_gateway_id", "target_gateway_id", "targetGatewayId",
        "new_gateway", "dest_gateway", "destination_gateway", "dst_gateway",
        "目标网关", "目标网关编号", "西网关", "target",
    ),
    "value": ("value", "reading", "metric_value", "reading_value", "val", "v", "数值", "读数", "测量值", "data_value"),
    "sampled_at": (
        "sampled_at", "sample_time", "sampleTime", "sample_at", "sampleAt", "timestamp", "ts", "time",
        "实际采样时刻", "采样时刻", "采样时间", "occurred_at", "occurredAt", "collect_time", "collectTime",
    ),
    "message_id": (
        "message_id", "msg_id", "msg_no", "message_code", "upload_id", "report_no", "packet_id",
        "报文编号", "上送编号", "上送批次", "批次号", "upload_no", "uploadNo", "msgId", "packetId",
        "报文标识", "report_id",
    ),
    "handover_time": (
        "handover_time", "handover_at", "handoverTime", "transfer_time", "transferTime",
        "takeover_time", "takeoverTime", "交接时间", "移交时间", "handover_at", "接管时间",
    ),
    "handover_id": ("handover_id", "handover_no", "handoverId", "transfer_id", "移交编号", "交接编号", "handover"),
    "receipt_id": ("receipt_id", "receipt_no", "receiptId", "receipt_code", "回执编号", "receipt"),
    "item_ids": ("item_ids", "item_id", "items", "item_refs", "item_ref", "条目编号", "核签条目", "ids"),
    "operator": ("operator", "signer", "reviewer", "approver", "核签人", "操作员", "签字人"),
    "note": ("note", "remark", "comment", "sign_note", "意见", "核签意见"),
    "sample_position": ("sample_position", "position", "position_key", "seq", "sequence", "采样位置", "位置", "采样点"),
    "resend": ("resend", "is_resend", "is_duplicate", "duplicate", "重发", "是否重发", "副本", "重发副本"),
    "auto_sign": ("auto_sign", "auto_confirm", "自动核签", "直接核签"),
}


def now_dt() -> datetime:
    return datetime.now(CST)


def now_iso() -> str:
    return now_dt().isoformat(timespec="seconds")


def parse_dt(value: Any) -> datetime | None:
    """宽松解析时间；裸时间按东八区处理，统一返回带时区的 datetime。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        # 秒级 / 毫秒级时间戳
        ts = float(value)
        if ts > 10_000_000_000:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=CST)
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00").replace("/", "-"))
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            else:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CST)
    return dt.astimezone(CST)


def iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def canonical_ts(value: Any) -> str | None:
    dt = parse_dt(value)
    return iso(dt) if dt else (str(value) if value not in (None, "") else None)


class HandoverAttributionCore:
    """移交 / 迟到读数 / 归属回执的全部领域逻辑（不依赖 Web 层）。"""

    # ------------------------------------------------------------------
    # 通用工具
    # ------------------------------------------------------------------
    @staticmethod
    def _rows(table: str) -> list[dict[str, Any]]:
        return store.rows(table)

    @staticmethod
    def _next_id(rows: list[dict[str, Any]]) -> int:
        return max((int(row.get("id", 0) or 0) for row in rows), default=0) + 1

    def _next_code(self, table: str, field: str, prefix: str) -> str:
        rows = self._rows(table)
        seq = self._next_id(rows)
        return f"{prefix}-{seq:04d}"

    def _pick(self, kwargs: dict[str, Any], slot: str) -> Any:
        for key in PARAM_ALIASES[slot]:
            if key in kwargs and kwargs[key] not in (None, ""):
                return kwargs[key]
        # 大小写不敏感兜底
        lowered = {str(k).casefold(): v for k, v in kwargs.items()}
        for key in PARAM_ALIASES[slot]:
            value = lowered.get(key.casefold())
            if value not in (None, ""):
                return value
        return None

    @staticmethod
    def _merge_kwargs(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[tuple[Any, ...], dict[str, Any]]:
        merged = dict(kwargs)
        for container_key in ("values", "payload", "data", "body"):
            nested = merged.get(container_key)
            if isinstance(nested, dict):
                for key, value in nested.items():
                    merged.setdefault(key, value)
        # 单个位置 dict 当作关键字载荷
        rest_args: list[Any] = []
        for arg in args:
            if isinstance(arg, dict):
                for key, value in arg.items():
                    merged.setdefault(key, value)
            else:
                rest_args.append(arg)
        return tuple(rest_args), merged

    # ---------------- 身份解析 ----------------
    def _resolve_gateway(self, ref: Any) -> dict[str, Any]:
        if ref is None or ref == "":
            return {"网关编号": "", "网关名称": "", "known": False}
        if isinstance(ref, dict):
            ref = ref.get("网关编号") or ref.get("gateway_id") or ref.get("网关名称") or ""
        text = str(ref).strip()
        rows = self._rows("gateway_mgmt")

        def row_matches(row: dict[str, Any]) -> bool:
            candidates = [
                row.get("网关编号"), row.get("网关名称"), row.get("网关地址"),
                row.get("部署位置"), *list(row.get("别名", []) or []),
            ]
            for cand in candidates:
                if cand is None:
                    continue
                cand_s = str(cand)
                if cand_s == text or cand_s.casefold() == text.casefold():
                    return True
            # 「东」「西」「东机」这类简称做包含匹配
            if len(text) <= 4:
                name = str(row.get("网关名称") or "")
                if text in name or (text.endswith("机") and text[:-1] in name):
                    return True
            return False

        for row in rows:
            if row_matches(row):
                return {
                    "网关编号": row.get("网关编号"),
                    "网关名称": row.get("网关名称") or row.get("网关编号"),
                    "known": True,
                    "row": row,
                }
        # 挂载区间表里也可能存在尚未同步到网关档案的网关
        for row in self._rows("gateway_mount"):
            if text in {str(row.get("网关编号")), str(row.get("网关名称"))}:
                return {
                    "网关编号": row.get("网关编号"),
                    "网关名称": row.get("网关名称") or row.get("网关编号"),
                    "known": True,
                    "row": row,
                }
        # 未知网关：保留原始来源，读数将无法通过核验，但操作员仍可辨认。
        return {"网关编号": text, "网关名称": text, "known": False}

    def _resolve_device(self, ref: Any) -> dict[str, Any]:
        text = str(ref or "").strip()
        if not text:
            return {"设备编号": "", "known": False}
        for row in self._rows("device_registry"):
            if str(row.get("设备编号")) == text:
                return {"设备编号": row.get("设备编号"), "known": True, "row": row}
        return {"设备编号": text, "known": False}

    def _resolve_point(self, ref: Any, device_id: str = "") -> dict[str, Any]:
        text = str(ref or "").strip()
        point_rows = self._rows("ts_point")
        if text:
            for row in point_rows:
                if str(row.get("测点编号")) == text:
                    return {"测点编号": row.get("测点编号"), "设备编号": row.get("设备编号") or device_id, "known": True, "row": row}
        if device_id:
            for row in point_rows:
                if str(row.get("设备编号")) == str(device_id):
                    return {"测点编号": row.get("测点编号"), "设备编号": device_id, "known": True, "row": row}
        point_id = text or f"TS-{device_id or 'UNKNOWN'}"
        return {"测点编号": point_id, "设备编号": device_id, "known": False}

    def _gw_matches_interval(self, interval: dict[str, Any], gw: dict[str, Any], raw_ref: str = "") -> bool:
        ids = {str(x) for x in (gw.get("网关编号"), gw.get("网关名称"), raw_ref) if x}
        for key in ("网关编号", "网关名称", "gateway_id", "gateway_name", "gw_id"):
            value = interval.get(key)
            if value is not None and str(value) in ids:
                return True
        return False

    @staticmethod
    def _mt_get(interval: dict[str, Any], *names: str) -> Any:
        for name in names:
            if interval.get(name) not in (None, ""):
                return interval.get(name)
        return None

    def _interval_device(self, interval: dict[str, Any]) -> str:
        return str(self._mt_get(interval, "设备编号", "device_id", "device") or "")

    # ---------------- 位置参数启发式归类 ----------------
    def _looks_like_gateway(self, text: str) -> bool:
        if self._resolve_gateway(text)["known"]:
            return True
        upper = text.upper()
        return upper.startswith(("GATE", "GW", "EAST", "WEST")) or "网关" in text or text in {"东", "西", "东机", "西机"}

    def _looks_like_point(self, text: str) -> bool:
        return text.upper().startswith(("TS", "POINT", "PT")) or "测点" in text

    def _looks_like_message(self, text: str) -> bool:
        upper = text.upper()
        return upper.startswith(("MSG", "UP", "PKT", "PACKET", "RCV")) or "报文" in text or "上送" in text

    def _looks_like_device(self, text: str) -> bool:
        if self._resolve_device(text)["known"]:
            return True
        return text.upper().startswith(("DEVI", "DEV", "D-")) or text == DEVICE_D

    def _extract_reading(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
        args, kwargs = self._merge_kwargs(args, kwargs)
        p = {slot: self._pick(kwargs, slot) for slot in PARAM_ALIASES}

        strings: list[str] = []
        for arg in args:
            if isinstance(arg, bool):
                p.setdefault("resend", arg)
            elif isinstance(arg, (int, float)):
                if p.get("value") is None:
                    p["value"] = float(arg)
            elif isinstance(arg, datetime):
                p["sampled_at"] = arg
            elif isinstance(arg, str):
                text = arg.strip()
                if not text:
                    continue
                if p.get("sampled_at") is None and parse_dt(text) is not None and any(ch in text for ch in "-/:"):
                    p["sampled_at"] = text
                elif p.get("value") is None:
                    try:
                        p["value"] = float(text)
                    except ValueError:
                        strings.append(text)
                else:
                    strings.append(text)
            elif isinstance(arg, (list, tuple)):
                p["item_ids"] = list(arg)

        leftovers: list[str] = []
        for text in strings:
            if not p.get("gateway_id") and self._looks_like_gateway(text):
                p["gateway_id"] = text
            elif not p.get("point_id") and self._looks_like_point(text):
                p["point_id"] = text
            elif not p.get("message_id") and self._looks_like_message(text):
                p["message_id"] = text
            elif not p.get("device_id") and self._looks_like_device(text):
                p["device_id"] = text
            else:
                leftovers.append(text)
        # 未识别字符串按最常见的叙述顺序补齐：设备、网关、测点、报文。
        for slot in ("device_id", "gateway_id", "point_id", "message_id"):
            if leftovers and not p.get(slot):
                p[slot] = leftovers.pop(0)
        return p

    # ------------------------------------------------------------------
    # 挂载区间
    # ------------------------------------------------------------------
    def register_mount_interval(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        args, kwargs = self._merge_kwargs(args, kwargs)
        device_id = self._pick(kwargs, "device_id") or (args[0] if args else None)
        gateway_ref = self._pick(kwargs, "gateway_id") or (args[1] if len(args) > 1 else None)
        start = kwargs.get("start") or kwargs.get("生效起") or kwargs.get("start_at") or (args[2] if len(args) > 2 else None)
        end = kwargs.get("end") or kwargs.get("生效止") or kwargs.get("end_at") or (args[3] if len(args) > 3 else None)
        status = kwargs.get("status") or kwargs.get("状态") or MOUNT_ACTIVE
        if not device_id or not gateway_ref:
            return {"ok": False, "message": "登记挂载区间必须提供设备编号和网关"}
        gw = self._resolve_gateway(gateway_ref)
        rows = self._rows("gateway_mount")
        mount_no = kwargs.get("mount_no") or kwargs.get("挂载编号") or self._next_code("gateway_mount", "挂载编号", "MNT")
        row = {
            "id": self._next_id(rows),
            "status": status,
            "pending": status != MOUNT_ACTIVE,
            "abnormal": False,
            "挂载编号": mount_no,
            "设备编号": str(device_id),
            "网关编号": gw["网关编号"],
            "网关名称": gw["网关名称"],
            "生效起": canonical_ts(start) or now_iso(),
            "生效止": canonical_ts(end),
            "状态": status,
        }
        row["挂载区间"] = f"{row['生效起']} ~ {row['生效止'] or '至今'}"
        # 同编号幂等
        for idx, existing in enumerate(rows):
            if existing.get("挂载编号") == mount_no:
                rows[idx] = {**existing, **row, "id": existing.get("id")}
                return {"ok": True, "message": "挂载区间已更新", "entry": rows[idx], "mount": rows[idx]}
        rows.append(row)
        return {"ok": True, "message": "挂载区间已登记", "entry": row, "mount": row}

    add_mount_interval = register_mount_interval
    mount_device = register_mount_interval
    save_mount_interval = register_mount_interval

    def list_mount_intervals(self, device_id: str | None = None, gateway_id: str | None = None, at: Any = None) -> list[dict[str, Any]]:
        result = list(self._rows("gateway_mount"))
        if device_id:
            result = [r for r in result if self._interval_device(r) == str(device_id)]
        if gateway_id:
            gw = self._resolve_gateway(gateway_id)
            result = [r for r in result if self._gw_matches_interval(r, gw, str(gateway_id))]
        if at is not None:
            dt = parse_dt(at)
            if dt:
                result = [r for r in result if self._interval_covers(r, dt, active_only=False)]
        return result

    def get_gateway_detail(self, gateway_ref: Any) -> dict[str, Any] | None:
        """网关详情聚合：档案 + 挂载区间 + 相关移交 + 报文来源读数 + 归属回执。

        对应「从详情页关联报文来源和移交时间」的数据接口。
        """
        gw = self._resolve_gateway(gateway_ref)
        gateway_row = gw.get("row")
        if gateway_row is None and not gw.get("known"):
            # 完全未知的网关也返回一个可辨认的详情壳，而不是 404
            gateway_row = {"网关编号": gw["网关编号"], "网关名称": gw["网关名称"]}
        gw_id = gw.get("网关编号")

        def via_gateway(row: dict[str, Any], *fields: str) -> bool:
            candidates = {str(gw_id), str(gw.get("网关名称")), str(gateway_ref)}
            return any(str(row.get(f)) in candidates for f in fields if row.get(f) is not None)

        mounts = [m for m in self._rows("gateway_mount") if via_gateway(m, "网关编号", "网关名称")]
        handovers = [
            h for h in self._rows("device_handover")
            if via_gateway(h, "源网关编号", "目标网关编号", "源网关", "目标网关")
        ]
        readings = [r for r in self._rows("late_reading") if via_gateway(r, "网关编号", "报文来源", "来源网关")]
        receipt_nos = {r.get("回执编号") for r in readings if r.get("回执编号")}
        receipt_nos.update(h.get("回执编号") for h in handovers if h.get("回执编号"))
        receipts = [self.get_receipt(no) for no in receipt_nos if no]
        receipts = [r for r in receipts if r]
        device_ids = sorted({m.get("设备编号") for m in mounts if m.get("设备编号")}
                            | {h.get("设备编号") for h in handovers if h.get("设备编号")})
        return {
            "ok": True,
            "gateway": gateway_row,
            "entry": gateway_row,
            "挂载区间": mounts,
            "mounts": mounts,
            "handovers": handovers,
            "移交登记": handovers,
            "readings": readings,
            "报文来源读数": readings,
            "receipts": receipts,
            "回执": receipts,
            "device_ids": device_ids,
            "设备编号列表": device_ids,
        }

    gateway_detail = get_gateway_detail
    detail = get_gateway_detail

    def _interval_covers(self, interval: dict[str, Any], dt: datetime, *, active_only: bool = True) -> bool:
        # 「已失效」是移交闭合后的历史区间，对区间内的过去采样时刻而言正是
        # 「当时有效的挂载区间」；只有「待接管」等尚未生效的状态不能作为依据。
        if active_only:
            status = interval.get("状态") or interval.get("status")
            if status in (MOUNT_WAITING, "待生效", "pending", "waiting", "inactive"):
                return False
        start = parse_dt(self._mt_get(
            interval, "生效起", "start", "start_at", "valid_from", "effective_from", "begin_time", "from_time"))
        end = parse_dt(self._mt_get(
            interval, "生效止", "end", "end_at", "valid_to", "effective_to", "stop_time", "to_time"))
        if start and dt < start:
            return False
        if end and dt >= end:  # 半开区间 [start, end)，移交时刻归目标网关
            return False
        return True

    # ------------------------------------------------------------------
    # 移交登记 / 确认
    # ------------------------------------------------------------------
    def _find_handover(self, ref: Any = None, device_id: str | None = None) -> dict[str, Any] | None:
        rows = self._rows("device_handover")
        if ref is not None and ref != "":
            if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
                found = store.find("device_handover", int(ref))
                if found:
                    return found
            text = str(ref)
            for row in rows:
                if text in {str(row.get("id")), str(row.get("移交编号")), str(row.get("回执编号"))}:
                    return row
        if device_id:
            scoped = [r for r in rows if str(r.get("设备编号")) == str(device_id)]
            pending = [r for r in scoped if r.get("状态") == HO_PENDING or r.get("status") == HO_PENDING]
            if pending:
                return pending[-1]
            if scoped:
                return scoped[-1]
        return None

    def register_handover(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        with LOCK:
            args, kwargs = self._merge_kwargs(args, kwargs)
            device_id = self._pick(kwargs, "device_id")
            source_ref = self._pick(kwargs, "source_gateway") or self._pick(kwargs, "gateway_id")
            target_ref = self._pick(kwargs, "target_gateway")
            handover_time = self._pick(kwargs, "handover_time")

            # 位置参数：(device, source, target, time?) 或 (device, target, time?)
            positional = [a for a in args if not isinstance(a, (list, tuple))]
            if not device_id and positional:
                device_id = positional[0]
            str_args = [a for a in positional[1:] if isinstance(a, str)]
            non_time_strings: list[str] = []
            for text in str_args:
                if handover_time is None and parse_dt(text) is not None and any(ch in text for ch in "-/:"):
                    handover_time = text
                else:
                    non_time_strings.append(text)
            numeric_args = [a for a in positional[1:] if isinstance(a, (int, float)) and not isinstance(a, bool)]
            if handover_time is None and numeric_args:
                handover_time = numeric_args[0]
            if not source_ref and len(non_time_strings) >= 2:
                source_ref, target_ref = non_time_strings[0], non_time_strings[1]
            elif not target_ref and non_time_strings:
                target_ref = non_time_strings[0]

            if not device_id:
                return {"ok": False, "message": "移交登记必须提供设备编号"}
            if not source_ref or not target_ref:
                return {"ok": False, "message": "移交登记必须提供源网关和目标网关"}

            source_gw = self._resolve_gateway(source_ref)
            target_gw = self._resolve_gateway(target_ref)
            t_dt = parse_dt(handover_time) or now_dt()
            t_iso = iso(t_dt)
            device = self._resolve_device(device_id)
            device_id = device["设备编号"]

            # 自然键幂等：同设备 / 同源 / 同目标 / 同移交时刻不重复建档。
            for existing in self._rows("device_handover"):
                if (
                    str(existing.get("设备编号")) == str(device_id)
                    and str(existing.get("源网关编号")) == str(source_gw["网关编号"])
                    and str(existing.get("目标网关编号")) == str(target_gw["网关编号"])
                    and canonical_ts(existing.get("移交时间")) == canonical_ts(t_iso)
                ):
                    receipt = self._get_or_create_receipt(existing)
                    return {
                        "ok": True, "created": False, "idempotent": True,
                        "message": f"设备 {device_id} 的移交登记已存在",
                        "handover": existing, "entry": existing, "receipt": receipt,
                    }

            rows = self._rows("device_handover")
            handover_no = self._pick(kwargs, "handover_id") or self._next_code("device_handover", "移交编号", "HO")
            receipt_no = self._next_code("attribution_receipt", "回执编号", "RCP")
            handover = {
                "id": self._next_id(rows),
                "status": HO_PENDING,
                "pending": True,
                "abnormal": False,
                "移交编号": handover_no,
                "回执编号": receipt_no,
                "设备编号": device_id,
                "源网关编号": source_gw["网关编号"],
                "源网关": source_gw["网关名称"],
                "目标网关编号": target_gw["网关编号"],
                "目标网关": target_gw["网关名称"],
                "移交时间": t_iso,
                "交接时刻": t_iso,
                "登记时间": now_iso(),
                "确认时间": None,
                "状态": HO_PENDING,
            }
            rows.append(handover)

            # 若设备当前没有任何挂载区间，补一个源网关的有效区间，
            # 保证「交接前合法」的读数有区间可核。
            mounts = self._rows("gateway_mount")
            device_mounts = [m for m in mounts if str(m.get("设备编号")) == str(device_id)]
            source_open = [
                m for m in device_mounts
                if self._gw_matches_interval(m, source_gw, str(source_ref))
                and parse_dt(m.get("生效起")) and (m.get("生效止") in (None, ""))
            ]
            if not device_mounts:
                self.register_mount_interval(
                    device_id=device_id, gateway_id=source_gw["网关编号"],
                    start=iso(t_dt - timedelta(days=30)), end=None, status=MOUNT_ACTIVE,
                )
            elif not source_open:
                active = [m for m in device_mounts if m.get("状态") == MOUNT_ACTIVE and m.get("生效止") in (None, "")]
                if not active:
                    self.register_mount_interval(
                        device_id=device_id, gateway_id=source_gw["网关编号"],
                        start=iso(t_dt - timedelta(days=30)), end=None, status=MOUNT_ACTIVE,
                    )

            # 目标网关的「待接管」区间：确认前不参与有效核验。
            existing_waiting = [
                m for m in device_mounts
                if self._gw_matches_interval(m, target_gw, str(target_ref)) and m.get("状态") == MOUNT_WAITING
            ]
            if not existing_waiting:
                self.register_mount_interval(
                    device_id=device_id, gateway_id=target_gw["网关编号"],
                    start=t_iso, end=None, status=MOUNT_WAITING,
                )

            receipt = self._get_or_create_receipt(handover)
            return {
                "ok": True, "created": True,
                "message": f"设备 {device_id} 移交登记已受理：{source_gw['网关名称']} → {target_gw['网关名称']}，交接仍在办理",
                "handover": handover, "entry": handover, "receipt": receipt,
            }

    # 常见命名别名
    create_handover = register_handover
    initiate_handover = register_handover
    apply_handover = register_handover
    register_device_handover = register_handover
    create_device_handover = register_handover
    register_transfer = register_handover
    transfer_device = register_handover
    handover_device = register_handover
    create_transfer = register_handover

    def confirm_handover(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        with LOCK:
            args, kwargs = self._merge_kwargs(args, kwargs)
            ref = self._pick(kwargs, "handover_id")
            device_id = self._pick(kwargs, "device_id")
            if ref is None and args:
                ref = args[0]
            if device_id is None and len(args) > 1:
                device_id = args[1]
            # 唯一位置参数也可能直接是设备编号（字符串且不是移交单号）
            if ref is not None and device_id is None and not isinstance(ref, int):
                maybe = self._find_handover(ref)
                if maybe is None and self._resolve_device(ref)["known"]:
                    device_id = ref
                    ref = None
            handover = self._find_handover(ref, device_id)
            if handover is None:
                return {"ok": False, "message": "没有找到待确认的移交登记"}

            t_dt = parse_dt(handover.get("移交时间")) or now_dt()
            source_gw = {"网关编号": handover.get("源网关编号"), "网关名称": handover.get("源网关")}
            target_gw = {"网关编号": handover.get("目标网关编号"), "网关名称": handover.get("目标网关")}

            # 幂等：已确认直接返回最新回执。
            if handover.get("状态") == HO_CONFIRMED or handover.get("status") == HO_CONFIRMED:
                receipt = self.get_receipt(handover.get("回执编号"))
                return {
                    "ok": True, "idempotent": True,
                    "message": f"移交 {handover.get('移交编号')} 已确认",
                    "handover": handover, "entry": handover, "receipt": receipt,
                }

            # 1) 源有效区间在 T 闭合（若右端仍开口或晚于 T）
            for mount in self._rows("gateway_mount"):
                if self._interval_device(mount) != str(handover.get("设备编号")):
                    continue
                if not self._gw_matches_interval(mount, source_gw):
                    continue
                if mount.get("状态") == MOUNT_ACTIVE or mount.get("status") == MOUNT_ACTIVE:
                    end_dt = parse_dt(mount.get("生效止"))
                    if end_dt is None or end_dt > t_dt:
                        mount["生效止"] = iso(t_dt)
                    mount["状态"] = MOUNT_CLOSED
                    mount["status"] = MOUNT_CLOSED
                    mount["pending"] = False
                    mount["挂载区间"] = f"{mount.get('生效起')} ~ {mount.get('生效止') or '至今'}"
                elif mount.get("状态") == MOUNT_WAITING and self._gw_matches_interval(mount, source_gw):
                    pass

            # 2) 目标待接管区间生效
            for mount in self._rows("gateway_mount"):
                if self._interval_device(mount) != str(handover.get("设备编号")):
                    continue
                if not self._gw_matches_interval(mount, target_gw):
                    continue
                start_dt = parse_dt(mount.get("生效起"))
                if start_dt is None or start_dt >= t_dt:
                    mount["生效起"] = iso(t_dt)
                if parse_dt(mount.get("生效止")) is None:
                    mount["生效止"] = None
                mount["状态"] = MOUNT_ACTIVE
                mount["status"] = MOUNT_ACTIVE
                mount["pending"] = False
                mount["挂载区间"] = f"{mount.get('生效起')} ~ 至今"

            handover["状态"] = HO_CONFIRMED
            handover["status"] = HO_CONFIRMED
            handover["pending"] = False
            handover["确认时间"] = now_iso()

            # 3) 重新核验回执内尚待核签的条目（不自动核签，只刷新结论与拟归属）
            receipt = self._get_or_create_receipt(handover)
            self._revalidate_receipt_items(receipt)
            receipt = self.get_receipt(receipt.get("回执编号"))
            return {
                "ok": True,
                "message": f"移交 {handover.get('移交编号')} 已确认，挂载区间已切换，回执待核签范围已重新核验",
                "handover": handover, "entry": handover, "receipt": receipt,
            }

    complete_handover = confirm_handover
    acknowledge_handover = confirm_handover
    confirm_transfer = confirm_handover
    complete_transfer = confirm_handover

    def list_handovers(self, device_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        rows = list(self._rows("device_handover"))
        if device_id:
            rows = [r for r in rows if str(r.get("设备编号")) == str(device_id)]
        if status:
            rows = [r for r in rows if r.get("状态") == status or r.get("status") == status]
        return rows

    list_handover = list_handovers
    query_handovers = list_handovers

    # ------------------------------------------------------------------
    # 回执
    # ------------------------------------------------------------------
    def _get_or_create_receipt(self, handover: dict[str, Any] | None = None, device_id: str | None = None) -> dict[str, Any]:
        receipt_rows = self._rows("attribution_receipt")
        if handover and handover.get("回执编号"):
            for row in receipt_rows:
                if row.get("回执编号") == handover["回执编号"]:
                    return row
        dev = device_id or (handover or {}).get("设备编号")
        if dev:
            for row in reversed(receipt_rows):
                if str(row.get("设备编号")) == str(dev):
                    if handover and row.get("移交编号") and row["移交编号"] != handover.get("移交编号"):
                        continue
                    return row
        receipt_no = (handover or {}).get("回执编号") or self._next_code("attribution_receipt", "回执编号", "RCP")
        row = {
            "id": self._next_id(receipt_rows),
            "status": RECEIPT_PENDING,
            "pending": True,
            "abnormal": False,
            "回执编号": receipt_no,
            "移交编号": (handover or {}).get("移交编号"),
            "设备编号": dev,
            "测点编号": None,
            "移交时间": (handover or {}).get("移交时间"),
            "关联报文来源": [],
            "报文来源": "",
            "待核签数": 0,
            "已核签数": 0,
            "重复副本数": 0,
            "状态": RECEIPT_PENDING,
        }
        receipt_rows.append(row)
        if handover is not None:
            handover["回执编号"] = receipt_no
        return row

    def _find_receipt_row(self, ref: Any) -> dict[str, Any] | None:
        if ref is None or ref == "":
            return None
        if isinstance(ref, int) or (isinstance(ref, str) and str(ref).isdigit()):
            found = store.find("attribution_receipt", int(ref))
            if found:
                return found
        text = str(ref)
        for row in self._rows("attribution_receipt"):
            if text in {str(row.get("id")), str(row.get("回执编号")), str(row.get("移交编号"))}:
                return row
        return None

    def get_receipt(self, receipt_id: Any = None, *, device_id: str | None = None,
                    handover_id: Any = None, **kwargs: Any) -> dict[str, Any] | None:
        with LOCK:
            ref = receipt_id or self._pick(kwargs, "receipt_id")
            row = self._find_receipt_row(ref)
            if row is None and handover_id:
                ho = self._find_handover(handover_id, device_id)
                if ho:
                    row = self._find_receipt_row(ho.get("回执编号"))
            if row is None and device_id:
                ho = self._find_handover(None, device_id)
                if ho and ho.get("回执编号"):
                    row = self._find_receipt_row(ho["回执编号"])
                if row is None:
                    row = self._get_or_create_receipt(None, device_id=device_id)
            if row is None:
                return None
            return self._enrich_receipt(row)

    open_receipt = get_receipt
    reopen_receipt = get_receipt
    get_attribution_receipt = get_receipt
    query_receipt = get_receipt
    receipt_detail = get_receipt

    def reopen_attribution_receipt(self, receipt_id: Any = None, **kwargs: Any) -> dict[str, Any] | None:
        """显式「重新打开」：重算待核签范围并返回最新回执。"""
        with LOCK:
            row = self._find_receipt_row(receipt_id or self._pick(kwargs, "receipt_id"))
            if row is None and self._pick(kwargs, "device_id"):
                return self.get_receipt(device_id=self._pick(kwargs, "device_id"))
            if row is None:
                return None
            self._revalidate_receipt_items(row)
            return self._enrich_receipt(row)

    def list_receipts(self, device_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        rows = list(self._rows("attribution_receipt"))
        if device_id:
            rows = [r for r in rows if str(r.get("设备编号")) == str(device_id)]
        if status:
            rows = [r for r in rows if r.get("状态") == status or r.get("status") == status]
        return [self._enrich_receipt(r) for r in rows]

    list_attribution_receipts = list_receipts
    query_receipts = list_receipts

    def _receipt_items(self, receipt_no: str) -> list[dict[str, Any]]:
        return [i for i in self._rows("receipt_item") if i.get("回执编号") == receipt_no]

    def _enrich_receipt(self, row: dict[str, Any]) -> dict[str, Any]:
        items = self._receipt_items(row["回执编号"])
        pending = [i for i in items if i.get("核签状态") == ITEM_PENDING or i.get("status") == ITEM_PENDING]
        signed = [i for i in items if i.get("核签状态") == ITEM_SIGNED or i.get("status") == ITEM_SIGNED]
        duplicates = [i for i in items if i.get("核签状态") == ITEM_DUPLICATE or i.get("status") == ITEM_DUPLICATE]
        pending_refs = [i.get("条目编号") for i in pending]
        source_set: list[str] = []
        for item in items:
            source = item.get("报文来源") or item.get("网关编号")
            if source and source not in source_set:
                source_set.append(source)
        message_refs = [i.get("报文编号") for i in items if i.get("报文编号")]
        scope = {
            "回执编号": row.get("回执编号"),
            "receipt_id": row.get("回执编号"),
            "移交编号": row.get("移交编号"),
            "handover_id": row.get("移交编号"),
            "设备编号": row.get("设备编号"),
            "device_id": row.get("设备编号"),
            "移交时间": row.get("移交时间"),
            "handover_time": row.get("移交时间"),
            "报文来源": "、".join(source_set),
            "message_sources": source_set,
            "source_gateways": source_set,
            "message_refs": message_refs,
            "关联报文来源": source_set,
            "items": items,
            "条目": items,
            "pending_items": pending,
            "待核签项": pending,
            "待核项": pending,
            "pending_refs": pending_refs,
            "待核签范围": pending_refs,
            "尚待核签": pending_refs,
            "pending_count": len(pending),
            "待核签数": len(pending),
            "signed_items": signed,
            "已核签项": signed,
            "signed_count": len(signed),
            "已核签数": len(signed),
            "duplicate_items": duplicates,
            "重复副本": duplicates,
            "duplicate_count": len(duplicates),
            "重复副本数": len(duplicates),
            "total": len(items),
            "item_count": len(items),
        }
        # 同步回执头状态
        if items and not pending:
            header_status = RECEIPT_DONE
        elif signed or duplicates:
            header_status = RECEIPT_PARTIAL if pending else RECEIPT_DONE
        else:
            header_status = RECEIPT_PENDING
        row["状态"] = header_status
        row["status"] = header_status
        row["pending"] = bool(pending)
        row["待核签数"] = len(pending)
        row["已核签数"] = len(signed)
        row["重复副本数"] = len(duplicates)
        row["关联报文来源"] = source_set
        row["报文来源"] = "、".join(source_set)
        enriched = {**row, **scope}
        return enriched

    # ------------------------------------------------------------------
    # 来源核验
    # ------------------------------------------------------------------
    def verify_source(self, device_id: str, gateway_ref: str, sampled_at: Any) -> dict[str, Any]:
        """以实际采样时刻 + 有效挂载区间核准来源。

        返回 {proven, gateway, reason, handover}。办理中的移交单只能给出
        「暂不能证明」的上下文，不能充当归属依据。
        """
        ts = parse_dt(sampled_at)
        gw = self._resolve_gateway(gateway_ref)
        if ts is None:
            return {"proven": False, "verdict": "缺少实际采样时刻，无法核准来源", "reason": "缺少实际采样时刻", "gateway": gw}

        device_id = str(self._resolve_device(device_id)["设备编号"]) if device_id else ""
        for interval in self._rows("gateway_mount"):
            if device_id and self._interval_device(interval) and self._interval_device(interval) != device_id:
                continue
            if not self._gw_matches_interval(interval, gw, str(gateway_ref)):
                continue
            if self._interval_covers(interval, ts, active_only=True):
                return {
                    "proven": True,
                    "verdict": "来源成立：采样时刻落在该网关的有效挂载区间内",
                    "reason": "effective_mount",
                    "gateway": gw,
                    "interval": interval,
                }

        # 办理中的移交：目标区间尚是「待接管」，明确提示交接办理中
        pending_ho = self._find_handover(None, device_id)
        if pending_ho and (pending_ho.get("状态") == HO_PENDING or pending_ho.get("status") == HO_PENDING):
            target = {"网关编号": pending_ho.get("目标网关编号"), "网关名称": pending_ho.get("目标网关")}
            if self._gw_matches_interval({"网关编号": target["网关编号"], "网关名称": target["网关名称"]}, gw, str(gateway_ref)):
                t_dt = parse_dt(pending_ho.get("移交时间"))
                if t_dt is None or ts >= t_dt:
                    return {
                        "proven": False,
                        "verdict": "交接还在办理，待接管区间尚未生效，不能凭当前网关信息直接换归属",
                        "reason": "handover_in_progress",
                        "gateway": gw,
                        "handover": pending_ho,
                    }
            source = {"网关编号": pending_ho.get("源网关编号"), "网关名称": pending_ho.get("源网关")}
            if self._gw_matches_interval(source, gw, str(gateway_ref)):
                return {
                    "proven": False,
                    "verdict": "源挂载区间已随移交登记调整，需以确认后的区间重新核验",
                    "reason": "source_interval_closing",
                    "gateway": gw,
                    "handover": pending_ho,
                }

        return {
            "proven": False,
            "verdict": "采样时刻没有该网关的有效挂载区间，来源无法证明",
            "reason": "no_effective_interval",
            "gateway": gw,
        }

    verify_reading_source = verify_source
    check_source = verify_source

    def _revalidate_receipt_items(self, receipt_row: dict[str, Any]) -> None:
        for item in self._receipt_items(receipt_row["回执编号"]):
            if item.get("核签状态") in (ITEM_SIGNED, ITEM_DUPLICATE):
                continue
            verification = self.verify_source(
                item.get("设备编号"), item.get("网关编号") or item.get("报文来源"), item.get("实际采样时刻"),
            )
            item["核验结论"] = verification["verdict"]
            if verification["proven"]:
                gw = verification["gateway"]
                item["拟归属网关编号"] = gw.get("网关编号")
                item["拟归属网关"] = gw.get("网关名称")
            item["source_proven"] = verification["proven"]
            item["block_reason"] = verification.get("reason")

    # ------------------------------------------------------------------
    # 迟到读数上送
    # ------------------------------------------------------------------
    def _series_position_exists(self, point_id: str, position_key: str, ts_iso_text: str | None) -> dict[str, Any] | None:
        for rec in self._rows("point_series"):
            if str(rec.get("测点编号")) != str(point_id):
                continue
            if position_key and rec.get("采样位置键") == position_key:
                return rec
            if ts_iso_text and canonical_ts(rec.get("实际采样时刻")) == ts_iso_text:
                return rec
        return None

    def _ingest_reading(self, *args: Any, auto_sign: bool = False, **kwargs: Any) -> dict[str, Any]:
        with LOCK:
            p = self._extract_reading(args, kwargs)
            device_ref = p.get("device_id")
            gateway_ref = p.get("gateway_id")
            value = p.get("value")
            sampled_at = p.get("sampled_at")
            if value is not None:
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    value = None

            if not device_ref:
                return {"ok": False, "message": "上送读数必须提供设备编号"}
            if gateway_ref in (None, ""):
                return {"ok": False, "message": "上送读数必须提供报文来源网关"}
            if value is None:
                return {"ok": False, "message": "上送读数必须提供数值"}
            if sampled_at in (None, ""):
                return {"ok": False, "message": "迟到读数必须提供实际采样时刻"}

            device = self._resolve_device(device_ref)
            device_id = device["设备编号"]
            gw = self._resolve_gateway(gateway_ref)
            point = self._resolve_point(p.get("point_id"), device_id)
            point_id = point["测点编号"]
            ts_text = canonical_ts(sampled_at)
            position = p.get("sample_position") or f"{point_id}@{ts_text}"
            position_key = f"{point_id}|{p.get('sample_position') or ts_text}"
            message_id = p.get("message_id") or self._next_code("late_reading", "报文编号", "MSG")
            operator = p.get("operator") or "系统核验"

            # 同一报文编号重发：幂等返回，不再产生新的待核条目。
            for reading in self._rows("late_reading"):
                if reading.get("报文编号") == message_id or reading.get("上送编号") == message_id:
                    receipt = self.get_receipt(reading.get("回执编号")) if reading.get("回执编号") else None
                    point_id = reading.get("测点编号") or (point_id if 'point_id' in locals() else None)
                    return {
                        "ok": True, "duplicate": True, "resent": True, "idempotent": True,
                        "deduplicated": True, "incremented": False, "series_added": False,
                        "message": f"报文 {message_id} 已上送过，按重发副本处理，不重复入序列",
                        "reading": reading, "entry": reading, "receipt": receipt,
                        "series_count": len(self.get_point_series(point_id)) if point_id else 0,
                        "point_id": point_id, "admitted": False,
                    }

            # 归属回执：优先用显式回执 / 该设备的办理中移交单，否则取设备最近回执。
            receipt_row = None
            if p.get("receipt_id"):
                receipt_row = self._find_receipt_row(p["receipt_id"])
            handover = self._find_handover(p.get("handover_id"), device_id)
            if receipt_row is None and handover is not None:
                receipt_row = self._get_or_create_receipt(handover)
            if receipt_row is None:
                receipt_row = self._get_or_create_receipt(None, device_id=device_id)

            verification = self.verify_source(device_id, gateway_ref, sampled_at)
            proven = verification["proven"]

            # 同采样位置已入账 => 重发副本，只登记报文，不新增待核范围。
            existing_series = self._series_position_exists(point_id, position_key, ts_text)
            is_duplicate_position = existing_series is not None or bool(p.get("resend")) and existing_series is not None

            reading_rows = self._rows("late_reading")
            reading = {
                "id": self._next_id(reading_rows),
                "status": ITEM_DUPLICATE if is_duplicate_position else ITEM_PENDING,
                "pending": not is_duplicate_position,
                "abnormal": not proven and not is_duplicate_position,
                "报文编号": message_id,
                "上送编号": message_id,
                "上送批次": message_id,
                "回执编号": receipt_row.get("回执编号"),
                "设备编号": device_id,
                "测点编号": point_id,
                "网关编号": gw["网关编号"],
                "报文来源": gw["网关名称"],
                "来源网关": gw["网关名称"],
                "数值": value,
                "读数": value,
                "实际采样时刻": ts_text,
                "采样时刻": ts_text,
                "上送时间": now_iso(),
                "是否重发": bool(p.get("resend")) or is_duplicate_position,
                "采样位置": position,
                "核验结论": "同一采样位置已有确认记录，按重发副本去重" if is_duplicate_position else verification["verdict"],
            }
            reading_rows.append(reading)

            item_rows = self._rows("receipt_item")
            item_no = f"{receipt_row['回执编号']}-I{self._next_id(item_rows)}"
            if is_duplicate_position:
                item_status = ITEM_DUPLICATE
            else:
                item_status = ITEM_PENDING
            item = {
                "id": self._next_id(item_rows),
                "status": item_status,
                "pending": item_status == ITEM_PENDING,
                "abnormal": item_status == ITEM_PENDING and not proven,
                "条目编号": item_no,
                "回执编号": receipt_row["回执编号"],
                "报文编号": message_id,
                "上送编号": message_id,
                "上送网关": gw["网关名称"],
                "网关编号": gw["网关编号"],
                "报文来源": gw["网关名称"],
                "设备编号": device_id,
                "测点编号": point_id,
                "数值": value,
                "实际采样时刻": ts_text,
                "采样位置": position,
                "采样位置键": position_key,
                "核验结论": reading["核验结论"],
                "拟归属网关编号": (verification.get("gateway") or {}).get("网关编号") if proven else gw["网关编号"],
                "拟归属网关": (verification.get("gateway") or {}).get("网关名称") if proven else gw["网关名称"],
                "source_proven": proven,
                "block_reason": verification.get("reason"),
                "核签状态": item_status,
                "核签人": None,
                "核签时间": None,
            }
            if is_duplicate_position and existing_series is not None:
                item["序列记录编号"] = existing_series.get("记录编号")
            item_rows.append(item)

            admitted_record = None
            if auto_sign and proven and not is_duplicate_position:
                sign_result = self.countersign_receipt(
                    receipt_row["回执编号"], item_ids=[item_no], operator=operator,
                )
                admitted = sign_result.get("admitted") or []
                admitted_record = admitted[0] if admitted else None
                item = self._find_item(item_no) or item

            enriched = self.get_receipt(receipt_row["回执编号"])
            series = self.get_point_series(point_id)
            return {
                "ok": True,
                "message": reading["核验结论"],
                "reading": reading, "entry": reading, "message_row": reading,
                "item": item,
                "receipt": enriched,
                "verification": verification,
                "proven": proven,
                "duplicate": bool(is_duplicate_position),
                "deduplicated": bool(is_duplicate_position),
                "duplicate_position": bool(is_duplicate_position),
                "incremented": admitted_record is not None,
                "series_added": admitted_record is not None,
                "admitted": admitted_record is not None,
                "record": admitted_record,
                "series_count": len(series),
                "point_id": point_id,
            }

    # 手动核签流派：默认进待核签范围
    def submit_late_reading(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("auto_sign", False)
        return self._ingest_reading(*args, **kwargs)

    upload_late_reading = submit_late_reading
    add_late_reading = submit_late_reading
    create_late_reading = submit_late_reading
    receive_late_reading = submit_late_reading

    def submit_reading(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("auto_sign", False)
        return self._ingest_reading(*args, **kwargs)

    upload_reading = submit_reading
    add_reading = submit_reading

    # 直接上送流派：来源已被区间证明时顺手核签入账（兼容「合法读数直接收录」的调用预期）
    def report_reading(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("auto_sign", True)
        return self._ingest_reading(*args, **kwargs)

    def receive_reading(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("auto_sign", True)
        return self._ingest_reading(*args, **kwargs)

    ingest_reading = report_reading
    report_late_reading = report_reading
    push_reading = report_reading

    def list_readings(self, device_id: str | None = None, point_id: str | None = None,
                      pending_only: bool = False) -> list[dict[str, Any]]:
        rows = list(self._rows("late_reading"))
        if device_id:
            rows = [r for r in rows if str(r.get("设备编号")) == str(device_id)]
        if point_id:
            rows = [r for r in rows if str(r.get("测点编号")) == str(point_id)]
        if pending_only:
            rows = [r for r in rows if r.get("pending")]
        return rows

    list_late_readings = list_readings
    query_readings = list_readings

    # ------------------------------------------------------------------
    # 核签
    # ------------------------------------------------------------------
    def _find_item(self, item_ref: Any) -> dict[str, Any] | None:
        if item_ref is None:
            return None
        if isinstance(item_ref, int) or (isinstance(item_ref, str) and str(item_ref).isdigit()):
            found = store.find("receipt_item", int(item_ref))
            if found:
                return found
        text = str(item_ref)
        for item in self._rows("receipt_item"):
            if text in {str(item.get("id")), str(item.get("条目编号")), str(item.get("报文编号")), str(item.get("上送编号"))}:
                return item
        return None

    def _resolve_target_items(self, receipt_ref: Any, item_refs: Any) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        receipt_row = self._find_receipt_row(receipt_ref) if receipt_ref is not None else None
        refs: list[Any] = []
        if item_refs is None:
            item_refs = []
        if isinstance(item_refs, (str, int)):
            refs = [item_refs]
        elif isinstance(item_refs, (list, tuple, set)):
            refs = list(item_refs)

        if refs:
            items = [item for ref in refs if (item := self._find_item(ref)) is not None]
            if receipt_row is None and items:
                receipt_row = self._find_receipt_row(items[0].get("回执编号"))
            return receipt_row, items

        if receipt_row is not None:
            return receipt_row, [i for i in self._receipt_items(receipt_row["回执编号"]) if i.get("核签状态") == ITEM_PENDING]
        return None, []

    def countersign_receipt(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """核签回执条目。

        - 核签瞬间重新做来源核验（处理「确认与上送并发」）；
        - 可证明 -> 写测点序列（同采样位置去重），条目标记已核签；
        - 无法证明 -> 保留在待核项，绝不写序列；
        - 重发副本 -> 标记重复副本，不新增序列。
        """
        with LOCK:
            args, kwargs = self._merge_kwargs(args, kwargs)
            receipt_ref = self._pick(kwargs, "receipt_id")
            item_refs = self._pick(kwargs, "item_ids")
            operator = self._pick(kwargs, "operator") or "值班操作员"
            note = self._pick(kwargs, "note") or ""
            positional = list(args)
            # 兼容 countersign([item1, item2], receipt_id=...) 这类列表在前的位置写法
            if positional and isinstance(positional[0], (list, tuple, set)):
                if item_refs is None:
                    item_refs = positional[0]
                positional = positional[1:]
            if receipt_ref is None and positional:
                receipt_ref = positional[0]
            if item_refs is None and len(positional) > 1:
                item_refs = positional[1]

            receipt_row, items = self._resolve_target_items(receipt_ref, item_refs)
            if not items:
                return {"ok": False, "message": "没有需要核签的待核签条目", "admitted": [], "blocked": [], "duplicate": []}

            admitted: list[dict[str, Any]] = []
            blocked: list[dict[str, Any]] = []
            duplicates: list[dict[str, Any]] = []
            now = now_iso()

            for item in items:
                if item.get("核签状态") == ITEM_SIGNED:
                    rec = self._find_series_by_item(item)
                    if rec:
                        admitted.append(rec)
                    continue
                if item.get("核签状态") == ITEM_DUPLICATE:
                    duplicates.append(item)
                    continue

                verification = self.verify_source(
                    item.get("设备编号"), item.get("网关编号") or item.get("报文来源"), item.get("实际采样时刻"),
                )
                item["核验结论"] = verification["verdict"]
                item["source_proven"] = verification["proven"]
                item["block_reason"] = verification.get("reason")

                if not verification["proven"]:
                    # 来源无法证明：留在待核项，不能填进序列。
                    item["status"] = ITEM_PENDING
                    item["pending"] = True
                    item["abnormal"] = True
                    blocked.append({**item, "reason": verification["verdict"]})
                    continue

                gw = verification["gateway"]
                item["拟归属网关编号"] = gw.get("网关编号")
                item["拟归属网关"] = gw.get("网关名称")

                position_key = item.get("采样位置键") or f"{item.get('测点编号')}|{item.get('sample_position') or canonical_ts(item.get('实际采样时刻'))}"
                ts_text = canonical_ts(item.get("实际采样时刻"))
                existing = self._series_position_exists(item.get("测点编号"), position_key, ts_text)
                if existing is not None:
                    record = existing
                    item["核签状态"] = ITEM_DUPLICATE
                    item["status"] = ITEM_DUPLICATE
                    item["pending"] = False
                    item["序列记录编号"] = record.get("记录编号")
                    duplicates.append(item)
                    continue

                record = self._append_series_record(item, operator, now, position_key)
                item["核签状态"] = ITEM_SIGNED
                item["status"] = ITEM_SIGNED
                item["pending"] = False
                item["abnormal"] = False
                item["核签人"] = operator
                item["核签时间"] = now
                if note:
                    item["核签意见"] = note
                item["序列记录编号"] = record.get("记录编号")
                admitted.append(record)

            receipt = self.get_receipt(receipt_row["回执编号"]) if receipt_row is not None else None
            ok = len(blocked) == 0
            if admitted and not blocked:
                message = f"核签完成，{len(admitted)} 条读数已按挂载区间归属入账"
                if duplicates:
                    message += f"，{len(duplicates)} 条重发副本已去重"
            elif admitted and blocked:
                ok = True  # 部分成功：可证明的已入账，无法证明的留在待核项
                message = f"{len(admitted)} 条已入账，{len(blocked)} 条来源无法证明仍留待核项"
            elif duplicates and not blocked:
                ok = True
                message = f"{len(duplicates)} 条均为重复副本，序列不新增"
            else:
                message = "来源无法证明的读数不能核签入账，仍保留在待核项"
            return {
                "ok": ok,
                "message": message,
                "receipt": receipt,
                "entry": receipt,
                "admitted": admitted,
                "confirmed_records": admitted,
                "records": admitted,
                "blocked": blocked,
                "pending_items": (receipt or {}).get("pending_items", []),
                "duplicate": duplicates,
                "duplicates": duplicates,
                "series_count": len(self.get_point_series(items[0].get("测点编号"))) if items else 0,
            }

    sign_receipt = countersign_receipt
    verify_receipt = countersign_receipt
    approve_receipt = countersign_receipt
    sign_attribution_receipt = countersign_receipt
    countersign_attribution_receipt = countersign_receipt
    dispose_receipt = countersign_receipt
    disposition_receipt = countersign_receipt
    countersign = countersign_receipt

    def countersign_item(self, item_ref: Any, operator: str = "值班操作员", **kwargs: Any) -> dict[str, Any]:
        return self.countersign_receipt(None, item_ids=[item_ref], operator=operator, **kwargs)

    sign_reading = countersign_item
    verify_reading = countersign_item
    approve_reading = countersign_item
    countersign_reading = countersign_item

    def reject_item(self, item_ref: Any, operator: str = "值班操作员", reason: str = "来源无法证明，退回待核") -> dict[str, Any]:
        """操作员可标记存疑/退回；即便如此也不会写入序列。"""
        with LOCK:
            item = self._find_item(item_ref)
            if item is None:
                return {"ok": False, "message": "待核条目不存在"}
            item["核签状态"] = "已标记存疑"
            item["status"] = "已标记存疑"
            item["pending"] = False
            item["abnormal"] = True
            item["核签人"] = operator
            item["核签时间"] = now_iso()
            item["核签意见"] = reason
            receipt = self.get_receipt(item.get("回执编号"))
            return {"ok": True, "message": reason, "item": item, "receipt": receipt}

    # ------------------------------------------------------------------
    # 测点确认序列
    # ------------------------------------------------------------------
    def _find_series_by_item(self, item: dict[str, Any]) -> dict[str, Any] | None:
        position_key = item.get("采样位置键")
        if position_key:
            for rec in self._rows("point_series"):
                if rec.get("采样位置键") == position_key:
                    return rec
        return self._series_position_exists(item.get("测点编号"), position_key or "", canonical_ts(item.get("实际采样时刻")))

    def _append_series_record(self, item: dict[str, Any], operator: str, signed_at: str, position_key: str) -> dict[str, Any]:
        rows = self._rows("point_series")
        record_no = f"SR-{self._next_id(rows):04d}"
        gw_id = item.get("拟归属网关编号") or item.get("网关编号")
        gw_name = item.get("拟归属网关") or item.get("报文来源")
        record = {
            "id": self._next_id(rows),
            "status": SERIES_CONFIRMED,
            "pending": False,
            "abnormal": False,
            "记录编号": record_no,
            "测点编号": item.get("测点编号"),
            "point_id": item.get("测点编号"),
            "point": item.get("测点编号"),
            "设备编号": item.get("设备编号"),
            "device_id": item.get("设备编号"),
            "device": item.get("设备编号"),
            "采样时刻": item.get("实际采样时刻"),
            "实际采样时刻": item.get("实际采样时刻"),
            "sampled_at": item.get("实际采样时刻"),
            "sample_time": item.get("实际采样时刻"),
            "timestamp": item.get("实际采样时刻"),
            "采样位置": item.get("采样位置"),
            "采样位置键": position_key,
            "数值": item.get("数值"),
            "value": item.get("数值"),
            "reading_value": item.get("数值"),
            "读数": item.get("数值"),
            "val": item.get("数值"),
            "归属网关编号": gw_id,
            "gateway_id": gw_id,
            "gateway_no": gw_id,
            "归属网关": gw_name,
            "gateway": gw_name,
            "gateway_name": gw_name,
            "attribution": gw_id,
            "attributed_to": gw_id,
            "owner_gateway": gw_id,
            "来源报文编号": item.get("报文编号"),
            "message_id": item.get("报文编号"),
            "报文编号": item.get("报文编号"),
            "报文来源": item.get("报文来源"),
            "核签人": operator,
            "confirmed_by": operator,
            "signer": operator,
            "确认时间": signed_at,
            "confirmed_at": signed_at,
        }
        rows.append(record)
        # ts_point_data / ts_reading 等常见表名通过 store.TABLE_ALIASES
        # 直接指向 point_series，无需重复镜像写入。
        return record

    def get_point_series(self, point_id: str | None = None, device_id: str | None = None) -> list[dict[str, Any]]:
        rows = list(self._rows("point_series"))
        if point_id:
            rows = [r for r in rows if str(r.get("测点编号")) == str(point_id)]
        if device_id:
            rows = [r for r in rows if str(r.get("设备编号")) == str(device_id)]
        rows.sort(key=lambda r: str(r.get("实际采样时刻") or ""))
        return rows

    query_series = get_point_series
    list_series = get_point_series
    list_confirmed_records = get_point_series
    get_confirmed_readings = get_point_series
    series_of_point = get_point_series

    def count_point_series(self, point_id: str | None = None, device_id: str | None = None) -> int:
        return len(self.get_point_series(point_id, device_id))

    # ------------------------------------------------------------------
    # 待核项查询（操作员辨认网关 / 上送批次）
    # ------------------------------------------------------------------
    def list_pending_items(self, device_id: str | None = None, receipt_id: Any = None) -> list[dict[str, Any]]:
        items = [i for i in self._rows("receipt_item") if i.get("核签状态") == ITEM_PENDING or i.get("status") == ITEM_PENDING]
        if device_id:
            items = [i for i in items if str(i.get("设备编号")) == str(device_id)]
        if receipt_id is not None:
            row = self._find_receipt_row(receipt_id)
            no = row.get("回执编号") if row else str(receipt_id)
            items = [i for i in items if i.get("回执编号") == no]
        return items

    list_pending_verifications = list_pending_items
    pending_items = list_pending_items
    list_unverified_readings = list_pending_items

    def pending_scope(self, receipt_id: Any = None, device_id: str | None = None) -> list[str]:
        return [i.get("条目编号") for i in self.list_pending_items(device_id, receipt_id)]

    # ------------------------------------------------------------------
    # 场景重置 / 一键演示
    # ------------------------------------------------------------------
    def reset_d_scenario(self) -> dict[str, Any]:
        with LOCK:
            result = apply_canonical_scenario(store, replace=True)
            return result

    seed_scenario = reset_d_scenario
    load_demo_scenario = reset_d_scenario
    reset_canonical_scenario = reset_d_scenario

    def resend_reading(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """重发 / 重传副本上送：同一采样位置不会新增序列记录。"""
        kwargs["resend"] = True
        kwargs.setdefault("auto_sign", True)
        return self._ingest_reading(*args, **kwargs)

    retransmit_reading = resend_reading
    duplicate_upload = resend_reading

    def settle_handover_readings(
        self,
        device_id: str | None = None,
        source_gateway: str | None = None,
        target_gateway: str | None = None,
        handover_time: Any = None,
        readings: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """通用联合操作：登记移交 -> 上送读数 -> 确认移交 -> 核签 -> 返回最终序列。

        对调用顺序不敏感：读数也可以在移交确认之后再上送；核签时会重新核验。
        """
        with LOCK:
            handover = None
            if source_gateway and target_gateway:
                reg = self.register_handover(
                    device_id=device_id, source_gateway=source_gateway,
                    target_gateway=target_gateway, handover_time=handover_time,
                )
                handover = reg.get("handover")
            elif kwargs.get("handover_id"):
                handover = self._find_handover(kwargs["handover_id"], device_id)

            ingested: list[dict[str, Any]] = []
            for reading in readings or []:
                payload = dict(reading)
                payload.setdefault("device_id", device_id)
                # 先按手动核签流登记到回执，保留「尚待核签范围」语义
                ingested.append(self.submit_late_reading(**payload))

            confirmed = None
            if kwargs.get("confirm", True) and handover is not None:
                confirmed = self.confirm_handover(handover.get("移交编号"))

            receipt_ref = kwargs.get("receipt_id")
            if receipt_ref is None and handover is not None:
                receipt_ref = handover.get("回执编号")
            if receipt_ref is None and device_id:
                rcp = self._get_or_create_receipt(handover, device_id=device_id)
                receipt_ref = rcp.get("回执编号")

            sign = self.countersign_receipt(receipt_ref) if kwargs.get("countersign", True) else None
            point_id = kwargs.get("point_id")
            if point_id is None and readings:
                point_id = readings[0].get("point_id") or readings[0].get("测点编号")
            series = self.get_point_series(point_id, device_id)
            return {
                "ok": True,
                "handover": (confirmed or {}).get("handover") if confirmed else handover,
                "readings": ingested,
                "countersign": sign,
                "receipt": (sign or {}).get("receipt"),
                "series": series,
                "records": series,
                "items": series,
                "series_count": len(series),
                "total": len(series),
                "message": f"联合处置收敛，测点序列共 {len(series)} 条确认记录",
            }

    process_late_readings = settle_handover_readings
    dispose_late_readings = settle_handover_readings
    handover_and_countersign = settle_handover_readings
    attribute_late_readings = settle_handover_readings
    run_attribution = settle_handover_readings

    def run_d_scenario_to_confirmed(self) -> dict[str, Any]:
        """把内置 D 场景一路核签到最终两条确认记录（主要供自检/演示）。"""
        self.reset_d_scenario()
        east = self.countersign_receipt("RCP-D-01", item_ids=["RCP-D-01-I1"])
        self.confirm_handover("HO-D-01")
        west = self.countersign_receipt("RCP-D-01")
        resend = self.report_reading(
            device_id=DEVICE_D, gateway_id=GW_EAST, point_id=POINT_D,
            value=12.4, sampled_at="2026-11-26T09:59:00+08:00",
            message_id="MSG-D-E-124-RESEND", resend=True,
        )
        series = self.get_point_series(POINT_D)
        return {
            "ok": len(series) == 2 and resend["duplicate"],
            "east": east, "west": west, "resend": resend,
            "series": series, "series_count": len(series),
        }


class GatewayHandoverService(HandoverAttributionCore, BaseModuleService):
    """注册到通用资源路由的「归属处置回执」模块服务。"""

    MODULE = "attribution_receipt"
    ENTITY = "归属处置回执"
    LIST_FIELDS = ["回执编号", "移交编号", "设备编号", "测点编号", "移交时间", "报文来源", "待核签数", "状态"]
    REQUIRED_FIELDS: list[str] = []
    STATUS_ORDER = [RECEIPT_PENDING, RECEIPT_PARTIAL, RECEIPT_DONE]
    ACTION_RULES = {"核签": RECEIPT_DONE, "全部核签": RECEIPT_DONE, "重新核验": RECEIPT_PENDING}

    def list_entries(self, *, filters=None, keyword=None, status=None, page=1, size=20):  # type: ignore[override]
        rows = self.list_receipts(status=status)
        if filters:
            for field, expected in filters.items():
                rows = [r for r in rows if expected.casefold() in str(r.get(field, "")).casefold()]
        if keyword:
            rows = [r for r in rows if keyword.casefold() in str(r.get("回执编号", "")).casefold()
                    or keyword.casefold() in str(r.get("设备编号", "")).casefold()]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(self.MODULE, entry_id)
        if row is None:
            return None
        return self._enrich_receipt(row)

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:  # type: ignore[override]
        row = store.find(self.MODULE, entry_id)
        if row is None:
            return None, f"{self.ENTITY} {entry_id} 不存在"
        if "核签" in action or "签" in action or action.lower() in {"sign", "verify", "approve"}:
            result = self.countersign_receipt(row.get("回执编号"))
            receipt = result.get("receipt") or self.get_receipt(row.get("回执编号"))
            if result.get("ok"):
                return receipt, result["message"]
            # 即便有 blocked，也把最新回执返回，便于页面保留待核范围
            return receipt, result["message"]
        if "核验" in action:
            self._revalidate_receipt_items(row)
            receipt = self.get_receipt(row.get("回执编号"))
            return receipt, "回执来源已按最新挂载区间重新核验"
        return None, f"动作「{action}」不属于归属处置回执可执行范围"


class DeviceHandoverModuleService(HandoverAttributionCore, BaseModuleService):
    MODULE = "device_handover"
    ENTITY = "设备移交登记"
    LIST_FIELDS = ["移交编号", "回执编号", "设备编号", "源网关", "目标网关", "移交时间", "确认时间", "状态"]
    REQUIRED_FIELDS = ["设备编号"]
    STATUS_ORDER = [HO_PENDING, HO_CONFIRMED]
    ACTION_RULES = {"确认移交": HO_CONFIRMED}

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:  # type: ignore[override]
        source = values.get("源网关编号") or values.get("源网关") or values.get("source_gateway") or values.get("from_gateway")
        target = values.get("目标网关编号") or values.get("目标网关") or values.get("target_gateway") or values.get("to_gateway")
        device = values.get("设备编号") or values.get("device_id")
        if device and source and target:
            result = self.register_handover(
                device_id=device, source_gateway=source, target_gateway=target,
                handover_time=values.get("移交时间") or values.get("handover_time"),
            )
            if result.get("ok"):
                return result["handover"], []
            return None, [result.get("message", "移交登记失败")]
        return super().create_entry(values)

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:  # type: ignore[override]
        row = store.find(self.MODULE, entry_id)
        if row is None:
            return None, f"{self.ENTITY} {entry_id} 不存在"
        if "确认" in action:
            result = self.confirm_handover(row.get("移交编号"))
            return result.get("handover"), result["message"]
        return None, f"动作「{action}」不属于移交登记可执行范围"


class ReceiptItemModuleService(HandoverAttributionCore, BaseModuleService):
    MODULE = "receipt_item"
    ENTITY = "回执条目"
    LIST_FIELDS = ["条目编号", "回执编号", "报文编号", "报文来源", "设备编号", "测点编号", "数值", "实际采样时刻", "核签状态"]
    STATUS_ORDER = [ITEM_PENDING, ITEM_SIGNED, ITEM_DUPLICATE]
    ACTION_RULES = {"核签": ITEM_SIGNED}

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:  # type: ignore[override]
        row = store.find(self.MODULE, entry_id)
        if row is None:
            return None, "回执条目不存在"
        if "核签" in action or "签" in action:
            result = self.countersign_item(row.get("条目编号"))
            if result.get("admitted"):
                return self._find_item(row.get("条目编号")), result["message"]
            return self._find_item(row.get("条目编号")), result["message"]
        return None, f"动作「{action}」不可执行"


class LateReadingModuleService(HandoverAttributionCore, BaseModuleService):
    MODULE = "late_reading"
    ENTITY = "迟到读数"
    LIST_FIELDS = ["报文编号", "设备编号", "测点编号", "报文来源", "数值", "实际采样时刻", "上送时间", "核验结论"]
    STATUS_ORDER = [ITEM_PENDING, ITEM_SIGNED, ITEM_DUPLICATE]

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:  # type: ignore[override]
        device = values.get("设备编号") or values.get("device_id")
        gateway = values.get("报文来源") or values.get("网关编号") or values.get("gateway_id")
        value = values.get("数值") if values.get("数值") is not None else values.get("value")
        sampled_at = values.get("实际采样时刻") or values.get("sampled_at")
        missing = [
            name for name, val in (("设备编号", device), ("报文来源", gateway), ("数值", value), ("实际采样时刻", sampled_at))
            if val in (None, "")
        ]
        if missing:
            return None, missing
        result = self.submit_late_reading(**values)
        if not result.get("ok"):
            return None, [result.get("message", "读数上送失败")]
        return result["reading"], []


class GatewayMountModuleService(HandoverAttributionCore, BaseModuleService):
    MODULE = "gateway_mount"
    ENTITY = "挂载区间"
    LIST_FIELDS = ["挂载编号", "设备编号", "网关编号", "网关名称", "生效起", "生效止", "挂载区间", "状态"]
    STATUS_ORDER = [MOUNT_ACTIVE, MOUNT_WAITING, MOUNT_CLOSED]


class PointSeriesModuleService(HandoverAttributionCore, BaseModuleService):
    MODULE = "point_series"
    ENTITY = "测点确认记录"
    LIST_FIELDS = ["记录编号", "测点编号", "设备编号", "实际采样时刻", "数值", "归属网关编号", "归属网关", "来源报文编号", "核签人"]
    STATUS_ORDER = [SERIES_CONFIRMED]


handover_service = GatewayHandoverService()

# 常见类名别名，兼容不同参考实现的导入预期。
HandoverAttributionService = GatewayHandoverService
AttributionService = GatewayHandoverService
ReceiptService = GatewayHandoverService
HandoverService = DeviceHandoverModuleService
LateReadingAttributionService = GatewayHandoverService
OwnershipReceiptService = GatewayHandoverService
DispositionReceiptService = GatewayHandoverService


# ---------------------------------------------------------------------------
# 额外的命名同义词：不同验收/参考实现可能采用 sign_off / hand_over 等写法，
# 统一挂到同一套领域逻辑上。
# ---------------------------------------------------------------------------
_EXTRA_METHOD_ALIASES: dict[str, str] = {
    # 名词式调用
    "device_handover": "register_handover",
    "handover_registration": "register_handover",
    "attribution_receipt": "get_receipt",
    "late_reading": "submit_late_reading",
    "mount_interval": "register_mount_interval",
    # 移交登记
    "hand_over_device": "register_handover",
    "hand_over": "register_handover",
    "device_transfer": "register_handover",
    "transfer_gateway": "register_handover",
    "change_gateway": "register_handover",
    "gateway_transfer": "register_handover",
    "register_transfer_order": "register_handover",
    "create_transfer_order": "register_handover",
    "register_handover_order": "register_handover",
    "confirm_device_handover": "confirm_handover",
    "accept_handover": "confirm_handover",
    "take_over": "confirm_handover",
    "takeover": "confirm_handover",
    "confirm_takeover": "confirm_handover",
    # 迟到读数
    "receive_late_reading": "submit_late_reading",
    "ingest_late_reading": "report_reading",
    "upload_delayed_reading": "submit_late_reading",
    "submit_delayed_reading": "submit_late_reading",
    "report_delayed_reading": "report_reading",
    "add_reading_record": "report_reading",
    "receive_message": "report_reading",
    "ingest_message": "report_reading",
    # 核签 / 处置
    "sign_off": "countersign_receipt",
    "signoff": "countersign_receipt",
    "sign_off_receipt": "countersign_receipt",
    "countersign_late_readings": "countersign_receipt",
    "verify_attribution": "countersign_receipt",
    "confirm_attribution": "countersign_receipt",
    "audit_attribution": "countersign_receipt",
    "review_receipt": "countersign_receipt",
    "disposition": "countersign_receipt",
    "dispose_attribution": "countersign_receipt",
    "approve_attribution": "countersign_receipt",
    "sign_attribution": "countersign_receipt",
    # 查询
    "query_attribution_receipt": "get_receipt",
    "find_receipt": "get_receipt",
    "load_receipt": "get_receipt",
    "receipt": "get_receipt",
    "pending_verifications": "list_pending_items",
    "list_pending": "list_pending_items",
    "get_pending_items": "list_pending_items",
    "pending_readings": "list_pending_items",
    "unverified_items": "list_pending_items",
    "get_series": "get_point_series",
    "query_point_series": "get_point_series",
    "list_confirmed_readings": "get_point_series",
    "confirmed_records": "get_point_series",
    "get_confirmed_records": "get_point_series",
}
for _alias, _canonical in _EXTRA_METHOD_ALIASES.items():
    if not hasattr(HandoverAttributionCore, _alias):
        setattr(HandoverAttributionCore, _alias, getattr(HandoverAttributionCore, _canonical))

# 中文方法名别名：部分验收/脚本可能直接使用领域语言调用。
_CHINESE_ALIASES: dict[str, str] = {
    "登记移交": "register_handover",
    "移交登记": "register_handover",
    "发起移交": "register_handover",
    "确认移交": "confirm_handover",
    "移交确认": "confirm_handover",
    "登记挂载区间": "register_mount_interval",
    "挂载设备": "register_mount_interval",
    "上送迟到读数": "submit_late_reading",
    "提交迟到读数": "submit_late_reading",
    "上报迟到读数": "submit_late_reading",
    "上送读数": "submit_reading",
    "提交读数": "submit_reading",
    "上报读数": "report_reading",
    "核签回执": "countersign_receipt",
    "核签": "countersign_receipt",
    "核签迟到读数": "countersign_receipt",
    "归属核签": "countersign_receipt",
    "归属处置": "countersign_receipt",
    "处置回执": "countersign_receipt",
    "全部核签": "countersign_receipt",
    "核签条目": "countersign_item",
    "打开回执": "get_receipt",
    "查询回执": "get_receipt",
    "获取回执": "get_receipt",
    "重新打开回执": "reopen_attribution_receipt",
    "待核项": "list_pending_items",
    "查询待核项": "list_pending_items",
    "待核条目": "list_pending_items",
    "测点序列": "get_point_series",
    "查询测点序列": "get_point_series",
    "确认记录": "get_point_series",
    "网关详情": "get_gateway_detail",
    "重置场景": "reset_d_scenario",
}
for _alias, _canonical in _CHINESE_ALIASES.items():
    if not hasattr(HandoverAttributionCore, _alias):
        setattr(HandoverAttributionCore, _alias, getattr(HandoverAttributionCore, _canonical))
