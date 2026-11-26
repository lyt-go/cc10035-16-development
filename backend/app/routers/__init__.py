"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import cross_module, handover_flow, resource

# handover_flow 含 /api/gateway_mgmt/... 的更具体子路径，必须在 resource
# 的 /api/{module}/{entry_id} 动态路由之前注册。
ROUTERS = [cross_module, handover_flow, resource]
