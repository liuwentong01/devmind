// 需求拆解 API（HITL 两阶段）：plan（拆解，暂停）→ confirm（确认写 Jira）。

export interface TaskItem {
  title: string;
  type: string;
  description: string;
  story_points: number | null;
  acceptance_criteria: string;
}

export interface PlanResult {
  thread_id: string;
  tasks: TaskItem[];
}

export interface ConfirmResult {
  created: { key: string; title: string }[];
  dry_run: boolean;
  project: string;
  epic: string | null;
}

export async function planRequirement(body: {
  prd_text?: string;
  confluence_url?: string;
  project?: string;
  epic?: string;
}): Promise<PlanResult> {
  const resp = await fetch('/api/requirement/plan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || `拆解失败：HTTP ${resp.status}`);
  return data as PlanResult;
}

export async function confirmRequirement(
  threadId: string,
  tasks: TaskItem[],
): Promise<ConfirmResult> {
  const resp = await fetch('/api/requirement/confirm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ thread_id: threadId, tasks }),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || `确认失败：HTTP ${resp.status}`);
  return data as ConfirmResult;
}
