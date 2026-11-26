/** 设备移交 + 迟到读数归属核签接口封装。
 * 后端对同一动作暴露了多组路径别名，这里使用语义最直接的一组。
 */
import { request } from './client'

async function postJson<T = any>(path: string, body: Record<string, unknown> = {}): Promise<T> {
  const response = await request(path, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  return (await response.json()) as T
}

async function getJson<T = any>(path: string): Promise<T> {
  const response = await request(path)
  if (!response.ok) throw new Error(`接口返回 ${response.status}`)
  return (await response.json()) as T
}

export type ApiResult = {
  ok: boolean
  message?: string
  [key: string]: any
}

export const handoverApi = {
  resetScenario: () => postJson<ApiResult>('/api/gateway_mgmt/scenario/reset'),
  runScenario: () => postJson<ApiResult>('/api/gateway_mgmt/scenario/run-confirmed'),

  listHandovers: (deviceId?: string) =>
    getJson<{ items: any[] }>(`/api/gateway_mgmt/handovers${deviceId ? `?device_id=${encodeURIComponent(deviceId)}` : ''}`),
  registerHandover: (values: Record<string, unknown>) =>
    postJson<ApiResult>('/api/gateway_mgmt/handovers', values),
  confirmHandover: (ref: string | number) =>
    postJson<ApiResult>(`/api/gateway_mgmt/handovers/${encodeURIComponent(String(ref))}/confirm`),

  listMounts: (deviceId?: string) =>
    getJson<{ items: any[] }>(`/api/gateway_mgmt/mounts${deviceId ? `?device_id=${encodeURIComponent(deviceId)}` : ''}`),

  getReceipt: (ref: string) =>
    getJson<ApiResult>(`/api/gateway_mgmt/receipts/${encodeURIComponent(ref)}`),
  reopenReceipt: (ref: string) =>
    getJson<ApiResult>(`/api/gateway_mgmt/receipts/${encodeURIComponent(ref)}/reopen`),
  countersign: (ref: string, itemIds?: string[]) =>
    postJson<ApiResult>(`/api/gateway_mgmt/receipts/${encodeURIComponent(ref)}/countersign`, {
      item_ids: itemIds,
    }),
  countersignItem: (itemRef: string) =>
    postJson<ApiResult>(`/api/gateway_mgmt/items/${encodeURIComponent(itemRef)}/countersign`),

  submitReading: (values: Record<string, unknown>) =>
    postJson<ApiResult>('/api/gateway_mgmt/readings', values),
  reportReading: (values: Record<string, unknown>) =>
    postJson<ApiResult>('/api/gateway_mgmt/readings/report', values),

  series: (pointId?: string, deviceId?: string) => {
    const qs = new URLSearchParams()
    if (pointId) qs.set('point_id', pointId)
    if (deviceId) qs.set('device_id', deviceId)
    const suffix = qs.toString() ? `?${qs.toString()}` : ''
    return getJson<{ items: any[] }>(`/api/gateway_mgmt/series${suffix}`)
  },
}
