// SSE 流式对话客户端。
// 浏览器原生 EventSource 不支持 POST，这里用 fetch + ReadableStream 手动解析 SSE。

export interface ApiChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
}

export interface StreamCallbacks {
  onToken: (token: string) => void;
  onTool: (tool: string) => void;
  onDone: () => void;
  onError: (message: string) => void;
}

interface SSEEvent {
  event: string;
  data: string;
}

/** 把 SSE 文本缓冲区按「空行」切分成一个个事件 */
function parseSSEChunk(buffer: string): { events: SSEEvent[]; rest: string } {
  const events: SSEEvent[] = [];
  const blocks = buffer.split('\n\n');
  // 最后一段可能不完整，留到下次拼接
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

export async function streamChat(
  messages: ApiChatMessage[],
  cb: StreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  try {
    const resp = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages }),
      signal,
    });

    if (!resp.ok || !resp.body) {
      cb.onError(`请求失败：HTTP ${resp.status}`);
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const { events, rest } = parseSSEChunk(buffer);
      buffer = rest;

      for (const ev of events) {
        switch (ev.event) {
          case 'token':
            cb.onToken(ev.data);
            break;
          case 'tool':
            cb.onTool(ev.data);
            break;
          case 'done':
            cb.onDone();
            return;
          case 'error':
            cb.onError(ev.data);
            return;
        }
      }
    }
    cb.onDone();
  } catch (err) {
    if ((err as Error).name === 'AbortError') return;
    cb.onError((err as Error).message);
  }
}
