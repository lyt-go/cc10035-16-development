"""领域服务注册表：通用资源路由按模块 key 分派到独立业务服务。"""
from __future__ import annotations

from app.services.base import BaseModuleService
from app.services.device_registry import DeviceRegistryService
from app.services.protocol_cfg import ProtocolCfgService
from app.services.msg_decode import MsgDecodeService
from app.services.msg_router import MsgRouterService
from app.services.ts_point import TsPointService
from app.services.ts_store import TsStoreService
from app.services.downsample import DownsampleService
from app.services.alarm_trigger import AlarmTriggerService
from app.services.firmware_ota import FirmwareOtaService
from app.services.gateway_mgmt import GatewayMgmtService
from app.services.shadow_dev import ShadowDevService
from app.services.data_gap import DataGapService
from app.services.batch_export import BatchExportService
from app.services.rule_engine import RuleEngineService
from app.services.edge_cache import EdgeCacheService
from app.services.tag_binding import TagBindingService
from app.services.data_stream import DataStreamService
from app.services.aggregate_sink import AggregateSinkService
from app.services.gateway_handover import (
    DeviceHandoverModuleService,
    GatewayHandoverService,
    GatewayMountModuleService,
    LateReadingModuleService,
    PointSeriesModuleService,
    ReceiptItemModuleService,
)

# 归属处置回执 / 移交登记等联合操作模块的单例
receipt_service = GatewayHandoverService()
handover_module_service = DeviceHandoverModuleService()
receipt_item_service = ReceiptItemModuleService()
late_reading_service = LateReadingModuleService()
gateway_mount_service = GatewayMountModuleService()
point_series_service = PointSeriesModuleService()

SERVICES: dict[str, BaseModuleService] = {
    "device_registry": DeviceRegistryService(),
    "protocol_cfg": ProtocolCfgService(),
    "msg_decode": MsgDecodeService(),
    "msg_router": MsgRouterService(),
    "ts_point": TsPointService(),
    "ts_store": TsStoreService(),
    "downsample": DownsampleService(),
    "alarm_trigger": AlarmTriggerService(),
    "firmware_ota": FirmwareOtaService(),
    "gateway_mgmt": GatewayMgmtService(),
    "shadow_dev": ShadowDevService(),
    "data_gap": DataGapService(),
    "batch_export": BatchExportService(),
    "rule_engine": RuleEngineService(),
    "edge_cache": EdgeCacheService(),
    "tag_binding": TagBindingService(),
    "data_stream": DataStreamService(),
    "aggregate_sink": AggregateSinkService(),
    # ---- 设备移交 + 迟到读数归属核签 ----
    "attribution_receipt": receipt_service,
    "receipt": receipt_service,
    "handover_receipt": receipt_service,
    "ownership_receipt": receipt_service,
    "disposition_receipt": receipt_service,
    "gateway_handover": handover_module_service,
    "device_handover": handover_module_service,
    "handover": handover_module_service,
    "transfer": handover_module_service,
    "receipt_item": receipt_item_service,
    "late_reading": late_reading_service,
    "reading": late_reading_service,
    "gateway_mount": gateway_mount_service,
    "mount_interval": gateway_mount_service,
    "mount": gateway_mount_service,
    "point_series": point_series_service,
    "ts_series": point_series_service,
}


def service_for(module: str) -> BaseModuleService | None:
    return SERVICES.get(module)
