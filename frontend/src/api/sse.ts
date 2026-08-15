// 通用 SSE 客户端：fetch + ReadableStream 手动解析（EventSource 不支持 POST）。

interface SSEEvent {
  event: string;
  data: string;
}

/** 按「空行」把缓冲区切成一个个 SSE 事件，返回事件列表与剩余不完整片段 */
function parseChunk(buffer: string): { events: SSEEvent[]; rest: string } {
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

export async function parseSSEStream(
  url: string,
  body: unknown,
  handlers: SSEHandlers,
  signal?: AbortSignal,
): Promise<void> {
  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    });

    if (!resp.ok || !resp.body) {
      handlers.error?.(`请求失败：HTTP ${resp.status}`);
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const { events, rest } = parseChunk(buffer);
      buffer = rest;
      for (const ev of events) {
        const stop = handlers[ev.event]?.(ev.data);
        if (ev.event === 'done' || ev.event === 'error' || stop === true) return;
      }
    }
  } catch (err) {
    if ((err as Error).name === 'AbortError') return;
    handlers.error?.((err as Error).message);
  }
}
