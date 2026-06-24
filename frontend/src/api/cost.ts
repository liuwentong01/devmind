// 成本治理 API。

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
}

export async function fetchCost(): Promise<CostSnapshot> {
  const resp = await fetch('/api/admin/cost');
  if (!resp.ok) throw new Error(`加载成本失败：HTTP ${resp.status}`);
  return resp.json();
}

export async function resetCost(): Promise<void> {
  await fetch('/api/admin/cost/reset', { method: 'POST' });
}
