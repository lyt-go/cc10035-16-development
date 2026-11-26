"""「归属处置回执 / disposition receipt」命名路径的兼容导出。"""
from __future__ import annotations

from app.services.gateway_handover import DispositionReceiptService

disposition_receipt_service = DispositionReceiptService()
service = disposition_receipt_service

__all__ = ["DispositionReceiptService", "disposition_receipt_service", "service"]
