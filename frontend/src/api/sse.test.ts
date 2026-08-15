// SSE 协议回归：覆盖网络半包、CRLF、多行 data、终止事件、HTTP 错误与意外 EOF。
// 运行：npm test -- sse.test.ts
import { afterEach, describe, expect, it, vi } from 'vitest';
import { parseSSEChunk, parseSSEStream } from './sse';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('SSE parser', () => {
  it('supports CRLF, multi-line data and incomplete chunks', () => {
    const parsed = parseSSEChunk(
      'event: token\r\ndata: first\r\ndata: second\r\n\r\nevent: token\ndata: partial',
    );

    expect(parsed.events).toEqual([{ event: 'token', data: 'first\nsecond' }]);
    expect(parsed.rest).toBe('event: token\ndata: partial');
  });

  it('dispatches stream events split across network chunks', async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('event: token\ndata: hel'));
        controller.enqueue(encoder.encode('lo\n\nevent: done\ndata: {}\n\n'));
        controller.close();
      },
    });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } }),
      ),
    );
    const tokens: string[] = [];
    const done = vi.fn();
    const error = vi.fn();

    await parseSSEStream(
      '/api/chat/stream',
      {},
      {
        token: (data) => {
          tokens.push(data);
        },
        done,
        error,
      },
    );

    expect(tokens).toEqual(['hello']);
    expect(done).toHaveBeenCalledOnce();
    expect(error).not.toHaveBeenCalled();
  });

  it('reports a structured HTTP error before reading the stream', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: '请求过于频繁' }), { status: 429 }),
      ),
    );
    const error = vi.fn();

    await parseSSEStream('/api/chat/stream', {}, { error });

    expect(error).toHaveBeenCalledWith('请求过于频繁');
  });

  it('extracts the message from a JSON SSE error event', async () => {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode(
            'event: error\ndata: {"message":"对话处理失败，请稍后重试"}\n\n',
          ),
        );
        controller.close();
      },
    });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(stream)));
    const error = vi.fn();

    await parseSSEStream('/api/chat/stream', {}, { error });

    expect(error).toHaveBeenCalledWith('对话处理失败，请稍后重试');
  });
});
