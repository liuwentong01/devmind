// 告警根因 API：流式根因分析（SSE：step 进度 + token 报告）。
import { parseSSEStream } from './sse';

export interface AlertInput {
  alertname: string;
  service: string;
  severity: string;
  description: string;
}

export interface RcaStep {
  node: string;
  label: string;
  detail: string;
}

export interface RcaCallbacks {
  onStep: (step: RcaStep) => void;
  onToken: (token: string) => void;
  onDone: () => void;
  onError: (message: string) => void;
}

export async function analyzeAlert(
  alert: AlertInput,
  cb: RcaCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  await parseSSEStream(
    '/api/incident/analyze/stream',
    alert,
    {
      step: (data) => cb.onStep(JSON.parse(data) as RcaStep),
      token: (data) => cb.onToken(data),
      done: () => cb.onDone(),
      error: (data) => cb.onError(data),
    },
    signal,
  );
}
