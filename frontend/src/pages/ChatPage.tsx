import { useRef, useState } from 'react';
import { Button, Input, Space, Tag, Typography } from 'antd';
import { ClearOutlined, SendOutlined } from '@ant-design/icons';
import { useAppDispatch, useAppSelector } from '../store/hooks';
import {
  addToolCall,
  addUserMessage,
  appendToken,
  clearChat,
  finishStreaming,
  setError,
  startAssistantMessage,
} from '../store/chatSlice';
import { streamChat, type ApiChatMessage } from '../api/chat';

const { Text } = Typography;

export default function ChatPage() {
  const dispatch = useAppDispatch();
  const { messages, streaming, error } = useAppSelector((s) => s.chat);
  const [input, setInput] = useState('');
  const listRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    requestAnimationFrame(() => {
      listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
    });
  };

  const handleSend = async () => {
    const content = input.trim();
    if (!content || streaming) return;

    // 1. 落用户消息，构造发给后端的历史
    const history: ApiChatMessage[] = messages.map((m) => ({ role: m.role, content: m.content }));
    history.push({ role: 'user', content });
    dispatch(addUserMessage(content));
    setInput('');

    // 2. 插入 assistant 占位，拿到它的 id 用于后续增量更新
    const action = dispatch(startAssistantMessage());
    const assistantId = action.payload.id;
    scrollToBottom();

    // 3. 启动 SSE 流式
    await streamChat(history, {
      onToken: (token) => {
        dispatch(appendToken({ id: assistantId, token }));
        scrollToBottom();
      },
      onTool: (tool) => dispatch(addToolCall({ id: assistantId, tool })),
      onDone: () => {
        dispatch(finishStreaming());
        scrollToBottom();
      },
      onError: (msg) => dispatch(setError(msg)),
    });
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div
        ref={listRef}
        style={{ flex: 1, overflowY: 'auto', padding: '16px 0', display: 'flex', flexDirection: 'column', gap: 12 }}
      >
        {messages.length === 0 && (
          <Text type="secondary" style={{ textAlign: 'center', marginTop: 80 }}>
            试试问：「现在几点了？再帮我算一下 12 *(3 + 4)」—— 观察 Agent 如何调用工具
          </Text>
        )}
        {messages.map((m) => (
          <div
            key={m.id}
            style={{ display: 'flex', justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start' }}
          >
            <div
              style={{
                maxWidth: '72%',
                padding: '10px 14px',
                borderRadius: 8,
                background: m.role === 'user' ? '#1677ff' : '#f5f5f5',
                color: m.role === 'user' ? '#fff' : 'rgba(0,0,0,0.88)',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}
            >
              {m.content || (streaming ? '思考中…' : '')}
              {m.tools && m.tools.length > 0 && (
                <div style={{ marginTop: 8 }}>
                  {m.tools.map((t) => (
                    <Tag key={t} color="geekblue" style={{ marginRight: 4 }}>
                      🔧 {t}
                    </Tag>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
        {error && <Text type="danger">出错了：{error}</Text>}
      </div>

      <Space.Compact style={{ width: '100%', paddingTop: 12 }}>
        <Input.TextArea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onPressEnter={(e) => {
            if (!e.shiftKey) {
              e.preventDefault();
              handleSend();
            }
          }}
          placeholder="输入消息，Enter 发送，Shift+Enter 换行"
          autoSize={{ minRows: 1, maxRows: 4 }}
          disabled={streaming}
        />
        <Button type="primary" icon={<SendOutlined />} loading={streaming} onClick={handleSend}>
          发送
        </Button>
        <Button icon={<ClearOutlined />} onClick={() => dispatch(clearChat())} disabled={streaming} />
      </Space.Compact>
    </div>
  );
}
