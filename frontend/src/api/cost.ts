// 成本治理 API。仅供本地 CostPage 或受审计管理通道使用；两个 endpoint 都要求
// 后端 admin key，普通生产浏览器不应直接获得管理凭据。
import { requestJson, requestVoid } from './client';

export interface ModelUsage {
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number;
}

export interface CostSnapshot {
  total_requests: number;
  total_cost_usd: number;
  by_model: Record<string, ModelUsage>;
  storage: 'disabled' | 'memory' | 'redis' | 'memory-fallback';
  degraded: boolean;
}

export async function fetchCost(): Promise<CostSnapshot> {
  return requestJson<CostSnapshot>('/api/admin/cost', {}, '加载成本失败');
}

export async function resetCost(): Promise<void> {
  await requestVoid('/api/admin/cost/reset', { method: 'POST' }, '重置成本失败');
}
