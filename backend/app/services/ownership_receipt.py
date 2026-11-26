"""「归属回执 / ownership receipt」命名路径的兼容导出。"""
from __future__ import annotations

from app.services.gateway_handover import OwnershipReceiptService

ownership_receipt_service = OwnershipReceiptService()
service = ownership_receipt_service

__all__ = ["OwnershipReceiptService", "ownership_receipt_service", "service"]
