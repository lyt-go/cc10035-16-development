"""「归属处置回执」模块的可能导入路径之一：具体实现见 gateway_handover。"""
from __future__ import annotations

from app.services.gateway_handover import (
    GatewayHandoverService,
    HandoverAttributionCore,
    handover_service,
)


class AttributionReceiptService(GatewayHandoverService):
    MODULE = "attribution_receipt"
    ENTITY = "归属处置回执"


class AttributionDispositionReceiptService(GatewayHandoverService):
    MODULE = "attribution_receipt"
    ENTITY = "归属处置回执"


attribution_receipt_service = AttributionReceiptService()
service = attribution_receipt_service

__all__ = [
    "AttributionReceiptService",
    "AttributionDispositionReceiptService",
    "HandoverAttributionCore",
    "attribution_receipt_service",
    "handover_service",
    "service",
]
