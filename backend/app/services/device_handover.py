"""「设备移交登记」模块的可能导入路径之一。"""
from __future__ import annotations

from app.services.gateway_handover import DeviceHandoverModuleService


class DeviceHandoverService(DeviceHandoverModuleService):
    pass


device_handover_service = DeviceHandoverService()
service = device_handover_service

__all__ = ["DeviceHandoverService", "device_handover_service", "service"]
