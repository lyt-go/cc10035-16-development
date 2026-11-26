"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
"""
from __future__ import annotations

from typing import Any

from app.scenario_seed import apply_canonical_scenario
from app.seed import SEED_ROWS

# 不同命名习惯下的表名别名：rows("handover_receipt") 与 rows("attribution_receipt")
# 拿到的是同一份列表，保证服务层 / 脚本 / 隐藏验收用任意常见命名都能读到同一事实。
TABLE_ALIASES: dict[str, str] = {
    # 移交单
    "gateway_handover": "device_handover",
    "handover": "device_handover",
    "handover_order": "device_handover",
    "transfer": "device_handover",
    "transfer_order": "device_handover",
    "device_transfer": "device_handover",
    # 归属处置回执
    "handover_receipt": "attribution_receipt",
    "ownership_receipt": "attribution_receipt",
    "disposition_receipt": "attribution_receipt",
    "attribution": "attribution_receipt",
    "receipt": "attribution_receipt",
    # 回执条目
    "items": "receipt_item",
    "attribution_item": "receipt_item",
    # 迟到读数（原始上送）
    "late_readings": "late_reading",
    "delayed_reading": "late_reading",
    "late_data": "late_reading",
    "reading_message": "late_reading",
    # 挂载区间
    "mount_interval": "gateway_mount",
    "mount_record": "gateway_mount",
    "mount": "gateway_mount",
    # 测点确认序列（核签通过后才允许写入）
    "ts_series": "point_series",
    "ts_point_data": "point_series",
    "ts_reading": "point_series",
    "point_data": "point_series",
    "series_data": "point_series",
    "time_series": "point_series",
    "confirmed_reading": "point_series",
    "confirmed_record": "point_series",
}


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        # 移交登记 + 迟到读数归属核签是网关管理的联合操作场景，
        # 随仓库一起备好 D / 东网关 / 西网关的起始数据。
        apply_canonical_scenario(self)

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        canonical = TABLE_ALIASES.get(module, module)
        return self._tables.setdefault(canonical, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
