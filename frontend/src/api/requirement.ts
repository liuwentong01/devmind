// 需求拆解 API（HITL 两阶段）：plan 返回 thread_id/可编辑任务，confirm 使用同一
// thread_id 提交人工批准内容。跨请求状态由后端 PostgreSQL 保存，不在本模块缓存。
import { jsonRequest, requestJson } from './client';

export interface TaskItem {
  title: string;
  type: 'backend' | 'frontend' | 'task';
  description: string;
  story_points: 1 | 2 | 3 | 5 | 8 | null;
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
  return requestJson<PlanResult>(
    '/api/requirement/plan',
    jsonRequest('POST', body),
    '拆解失败',
  );
}

export async function confirmRequirement(
  threadId: string,
  tasks: TaskItem[],
): Promise<ConfirmResult> {
  return requestJson<ConfirmResult>(
    '/api/requirement/confirm',
    jsonRequest('POST', { thread_id: threadId, tasks }),
    '确认失败',
  );
}
