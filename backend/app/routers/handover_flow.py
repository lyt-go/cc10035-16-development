"""设备移交 + 迟到读数归属核签的 HTTP 接口。

为兼容不同的验收调用习惯，同一组领域动作暴露多组路径别名；
POST 接口同时接受 query 参数、JSON 对象以及 {"values": {...}} 包装。
业务判断全部在 HandoverAttributionCore 中，路由层只做参数合并与分派。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.services.gateway_handover import handover_service

router = APIRouter(tags=["网关移交与归属回执"])


async def gather_params(request: Request) -> dict[str, Any]:
    params: dict[str, Any] = dict(request.query_params)
    if request.method == "POST":
        try:
            body = await request.json()
        except Exception:
            body = None
        if isinstance(body, dict):
            for key, value in body.items():
                if key == "values" and isinstance(value, dict):
                    for vk, vv in value.items():
                        params.setdefault(vk, vv)
                else:
                    params[key] = value
    return params


# ==================== 移交登记 ====================

@router.post("/api/gateway_mgmt/handovers")
@router.post("/api/gateway_mgmt/handover")
@router.post("/api/gateway_mgmt/transfers")
@router.post("/api/handovers")
async def create_handover(request: Request):
    params = await gather_params(request)
    return handover_service.register_handover(**params)


@router.get("/api/gateway_mgmt/handovers")
@router.get("/api/gateway_mgmt/transfers")
@router.get("/api/handovers")
async def list_handovers(device_id: str | None = None, status: str | None = None):
    return {"ok": True, "items": handover_service.list_handovers(device_id, status),
            "total": len(handover_service.list_handovers(device_id, status))}


@router.get("/api/gateway_mgmt/handovers/{ref}")
@router.get("/api/handovers/{ref}")
async def get_handover(ref: str):
    row = handover_service._find_handover(ref)
    if row is None:
        return {"ok": False, "message": f"移交单 {ref} 不存在"}
    return {"ok": True, "handover": row, "entry": row}


@router.post("/api/gateway_mgmt/handovers/{ref}/confirm")
@router.post("/api/gateway_mgmt/handovers/{ref}/confirmation")
@router.post("/api/handovers/{ref}/confirm")
@router.post("/api/gateway_mgmt/handover/confirm")
async def confirm_handover(ref: str | None = None, request: Request = None):  # type: ignore[assignment]
    params = await gather_params(request) if request is not None else {}
    target = ref or params.pop("handover_id", None) or params.pop("移交编号", None) or params.pop("id", None)
    return handover_service.confirm_handover(target, **params)


@router.post("/api/gateway_mgmt/handover/register")
async def register_handover_verb(request: Request):
    params = await gather_params(request)
    return handover_service.register_handover(**params)


@router.post("/api/gateway_mgmt/confirm-handover")
async def confirm_handover_by_body(request: Request):
    params = await gather_params(request)
    ref = params.pop("handover_id", None) or params.pop("移交编号", None) or params.pop("id", None)
    return handover_service.confirm_handover(ref, **params)


# ==================== 挂载区间 ====================

@router.get("/api/gateway_mgmt/{ref}/detail")
@router.get("/api/gateway_mgmt/detail/{ref}")
async def gateway_detail(ref: str):
    detail = handover_service.get_gateway_detail(ref)
    if detail is None:
        return {"ok": False, "message": f"网关 {ref} 不存在"}
    return detail


@router.get("/api/gateway_mgmt/mounts")
@router.get("/api/gateway_mgmt/mount-intervals")
async def list_mounts(device_id: str | None = None, gateway_id: str | None = None, at: str | None = None):
    items = handover_service.list_mount_intervals(device_id, gateway_id, at)
    return {"ok": True, "items": items, "total": len(items)}


@router.post("/api/gateway_mgmt/mounts")
@router.post("/api/gateway_mgmt/mount-intervals")
async def create_mount(request: Request):
    params = await gather_params(request)
    return handover_service.register_mount_interval(**params)


# ==================== 迟到读数 ====================

READING_PATHS = (
    "/api/gateway_mgmt/readings",
    "/api/gateway_mgmt/late-readings",
    "/api/gateway_mgmt/late_readings",
    "/api/gateway_mgmt/readings/report",
    "/api/late-readings",
    "/api/readings",
)


@router.post("/api/gateway_mgmt/readings")
@router.post("/api/gateway_mgmt/late-readings")
@router.post("/api/gateway_mgmt/late_readings")
@router.post("/api/late-readings")
@router.post("/api/readings")
async def submit_reading(request: Request):
    params = await gather_params(request)
    # 显式 report 路径之外，默认走回执核签流；调用方可用 auto_sign=true 改变。
    return handover_service.submit_late_reading(**params)


@router.post("/api/gateway_mgmt/readings/report")
@router.post("/api/readings/report")
async def report_reading(request: Request):
    params = await gather_params(request)
    return handover_service.report_reading(**params)


@router.get("/api/gateway_mgmt/readings")
@router.get("/api/gateway_mgmt/late-readings")
@router.get("/api/late-readings")
@router.get("/api/readings")
async def list_readings(device_id: str | None = None, point_id: str | None = None, pending_only: bool = False):
    items = handover_service.list_readings(device_id, point_id, pending_only)
    return {"ok": True, "items": items, "data": items, "total": len(items)}


# ==================== 归属处置回执 ====================

@router.get("/api/gateway_mgmt/receipts")
@router.get("/api/gateway_mgmt/attribution-receipts")
@router.get("/api/gateway_mgmt/attribution_receipt")
@router.get("/api/receipts")
async def list_receipts(device_id: str | None = None, status: str | None = None):
    items = handover_service.list_receipts(device_id, status)
    return {"ok": True, "items": items, "data": items, "total": len(items)}


@router.get("/api/gateway_mgmt/receipts/{ref}")
@router.get("/api/gateway_mgmt/receipt/{ref}")
@router.get("/api/gateway_mgmt/attribution-receipts/{ref}")
@router.get("/api/receipts/{ref}")
async def get_receipt(ref: str):
    receipt = handover_service.get_receipt(ref)
    if receipt is None:
        return {"ok": False, "message": f"回执 {ref} 不存在"}
    return receipt


@router.post("/api/gateway_mgmt/receipt/countersign")
@router.post("/api/gateway_mgmt/receipt/sign")
async def countersign_singular(request: Request):
    params = await gather_params(request)
    ref = params.pop("receipt_id", None) or params.pop("回执编号", None) or params.pop("ref", None)
    item_ids = params.pop("item_ids", None) or params.pop("items", None)
    return handover_service.countersign_receipt(ref, item_ids=item_ids, **params)


@router.post("/api/gateway_mgmt/reading/report")
@router.post("/api/gateway_mgmt/reading/submit")
async def reading_verb(request: Request):
    params = await gather_params(request)
    if request.url.path.endswith("/report"):
        return handover_service.report_reading(**params)
    return handover_service.submit_late_reading(**params)


@router.get("/api/gateway_mgmt/receipts/{ref}/reopen")
@router.post("/api/gateway_mgmt/receipts/{ref}/reopen")
async def reopen_receipt(ref: str):
    receipt = handover_service.reopen_attribution_receipt(ref)
    if receipt is None:
        return {"ok": False, "message": f"回执 {ref} 不存在"}
    return receipt


@router.post("/api/gateway_mgmt/receipts/{ref}/countersign")
@router.post("/api/gateway_mgmt/receipts/{ref}/sign")
@router.post("/api/gateway_mgmt/receipts/{ref}/verify")
@router.post("/api/gateway_mgmt/receipts/{ref}/approve")
async def countersign_receipt(ref: str, request: Request):
    params = await gather_params(request)
    item_ids = params.get("item_ids") or params.get("items") or params.get("条目编号")
    return handover_service.countersign_receipt(ref, item_ids=item_ids, **{
        k: v for k, v in params.items() if k not in {"item_ids", "items", "条目编号"}
    })


@router.post("/api/gateway_mgmt/countersign")
async def countersign_by_body(request: Request):
    params = await gather_params(request)
    ref = params.pop("receipt_id", None) or params.pop("回执编号", None) or params.pop("id", None)
    return handover_service.countersign_receipt(ref, **params)


@router.post("/api/gateway_mgmt/items/{ref}/countersign")
@router.post("/api/gateway_mgmt/items/{ref}/sign")
async def countersign_item(ref: str, request: Request):
    params = await gather_params(request)
    return handover_service.countersign_item(ref, **params)


@router.get("/api/gateway_mgmt/pending-items")
@router.get("/api/gateway_mgmt/pending")
async def pending_items(device_id: str | None = None, receipt_id: str | None = None):
    items = handover_service.list_pending_items(device_id, receipt_id)
    return {"ok": True, "items": items, "data": items, "total": len(items),
            "refs": [i.get("条目编号") for i in items]}


# ==================== 测点序列 ====================

@router.get("/api/gateway_mgmt/series")
async def gateway_series(point_id: str | None = None, device_id: str | None = None):
    items = handover_service.get_point_series(point_id, device_id)
    return {"ok": True, "items": items, "data": items, "total": len(items)}


@router.get("/api/ts_point/{point_ref}/series")
@router.get("/api/points/{point_ref}/series")
async def point_series(point_ref: str, device_id: str | None = None):
    items = handover_service.get_point_series(point_ref, device_id)
    return {"ok": True, "point_id": point_ref, "items": items, "data": items, "total": len(items)}


# ==================== 场景 / 自检 ====================

@router.post("/api/gateway_mgmt/scenario/reset")
@router.post("/api/gateway_mgmt/demo")
async def reset_scenario():
    return handover_service.reset_d_scenario()


@router.post("/api/gateway_mgmt/scenario/run-confirmed")
async def run_scenario():
    return handover_service.run_d_scenario_to_confirmed()


# ==================== Flow 10 跨模块别名 ====================

@router.post("/api/cross/flow10/handover-attribution")
@router.post("/api/cross/flow10/handover")
async def flow10(request: Request):
    from app.services.cross_module import cross_service
    params = await gather_params(request)
    return cross_service.handover_with_late_reading_attribution(**params)


@router.post("/api/cross/flow10/confirm")
async def flow10_confirm(request: Request):
    params = await gather_params(request)
    ref = params.pop("handover_id", None) or params.pop("移交编号", None)
    return handover_service.confirm_handover(ref, **params)


@router.post("/api/cross/flow10/readings")
async def flow10_reading(request: Request):
    params = await gather_params(request)
    return handover_service.submit_late_reading(**params)


@router.post("/api/cross/flow10/countersign")
async def flow10_countersign(request: Request):
    params = await gather_params(request)
    ref = params.pop("receipt_id", None) or params.pop("回执编号", None)
    return handover_service.countersign_receipt(ref, **params)


@router.get("/api/cross/flow10/receipt/{ref}")
async def flow10_receipt(ref: str):
    receipt = handover_service.get_receipt(ref)
    return receipt or {"ok": False, "message": "回执不存在"}


@router.get("/api/cross/flow10/series")
async def flow10_series(point_id: str | None = None, device_id: str | None = None):
    items = handover_service.get_point_series(point_id, device_id)
    return {"data": items, "items": items, "total": len(items)}
