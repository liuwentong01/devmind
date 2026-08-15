// 通用 SSE 客户端：fetch + ReadableStream 手动解析（EventSource 不支持 POST）。
// 使用：各场景传 URL、JSON body 与事件 handler；函数在 done/error 后主动结束 reader。
import { apiFetch, responseErrorMessage } from './client';

interface SSEEvent {
  event: string;
  data: string;
}

/** 按「空行」把缓冲区切成一个个 SSE 事件，返回事件列表与剩余不完整片段 */
export function parseSSEChunk(buffer: string): { events: SSEEvent[]; rest: string } {
  const events: SSEEvent[] = [];
  const normalized = buffer.replace(/\r\n/g, '\n');
  const blocks = normalized.split('\n\n');
  const rest = blocks.pop() ?? '';

  for (const block of blocks) {
    let event = 'message';
    const dataLines: string[] = [];
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim();
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''));
    }
    if (dataLines.length > 0) events.push({ event, data: dataLines.join('\n') });
  }
  return { events, rest };
}

/** 事件名 → 处理函数。返回 true 表示流应结束 */
export type SSEHandlers = Record<string, (data: string) => void | boolean>;

export function sseErrorMessage(data: string): string {
  try {
    const payload = JSON.parse(data) as { message?: unknown; detail?: unknown };
    if (typeof payload.message === 'string') return payload.message;
    if (typeof payload.detail === 'string') return payload.detail;
  } catch {
    // Plain text is also a valid SSE error payload.
  }
  return data;
}

export async function parseSSEStream(
  url: string,
  body: unknown,
  handlers: SSEHandlers,
  signal?: AbortSignal,
): Promise<void> {
  try {
    const resp = await apiFetch(
      url,
      {
        method: 'POST',
        headers: {
          Accept: 'text/event-stream',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(body),
        signal,
      },
      { timeoutMs: null },
    );

    if (!resp.ok) {
      handlers.error?.(await responseErrorMessage(resp));
      return;
    }
    if (!resp.body) {
      handlers.error?.('服务端未返回可读取的事件流');
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let terminalEventReceived = false;

    const dispatchEvents = (events: SSEEvent[]) => {
      for (const event of events) {
        const data = event.event === 'error' ? sseErrorMessage(event.data) : event.data;
        const stop = handlers[event.event]?.(data);
        if (event.event === 'done' || event.event === 'error' || stop === true) {
          terminalEventReceived = true;
          return true;
        }
      }
      return false;
    };

    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const { events, rest } = parseSSEChunk(buffer);
      buffer = rest;
      if (dispatchEvents(events)) {
        await reader.cancel();
        return;
      }
    }

    buffer += decoder.decode();
    if (buffer.trim()) {
      const { events } = parseSSEChunk(`${buffer}\n\n`);
      dispatchEvents(events);
    }
    if (!terminalEventReceived) handlers.error?.('事件流意外中断，请稍后重试');
  } catch (err) {
    if ((err as Error).name === 'AbortError') return;
    handlers.error?.((err as Error).message || '网络请求失败');
  }
}
