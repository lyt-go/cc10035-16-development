"""「迟到读数」模块的可能导入路径之一。"""
from __future__ import annotations

from app.services.gateway_handover import LateReadingModuleService


class LateReadingService(LateReadingModuleService):
    pass


late_reading_service = LateReadingService()
service = late_reading_service

__all__ = ["LateReadingService", "late_reading_service", "service"]
