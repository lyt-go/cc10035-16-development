"""「移交回执」命名路径的兼容导出。"""
from __future__ import annotations

from app.services.gateway_handover import GatewayHandoverService


class HandoverReceiptService(GatewayHandoverService):
    MODULE = "attribution_receipt"
    ENTITY = "移交归属回执"


handover_receipt_service = HandoverReceiptService()
service = handover_receipt_service

__all__ = ["HandoverReceiptService", "handover_receipt_service", "service"]
