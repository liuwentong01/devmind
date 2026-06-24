import { createSlice, nanoid, type PayloadAction } from '@reduxjs/toolkit';

export type Role = 'user' | 'assistant';

export interface Message {
  id: string;
  role: Role;
  content: string;
  /** 本条 assistant 消息期间 Agent 调用过的工具名 */
  tools?: string[];
}

interface ChatState {
  messages: Message[];
  /** 是否正在等待 / 接收流式响应 */
  streaming: boolean;
  error: string | null;
}

const initialState: ChatState = {
  messages: [],
  streaming: false,
  error: null,
};

const chatSlice = createSlice({
  name: 'chat',
  initialState,
  reducers: {
    // 追加一条用户消息
    addUserMessage: {
      reducer(state, action: PayloadAction<Message>) {
        state.messages.push(action.payload);
      },
      prepare(content: string) {
        return { payload: { id: nanoid(), role: 'user' as Role, content } };
      },
    },
    // 流式开始：插入一条空的 assistant 占位消息
    startAssistantMessage: {
      reducer(state, action: PayloadAction<Message>) {
        state.messages.push(action.payload);
        state.streaming = true;
        state.error = null;
      },
      prepare() {
        return {
          payload: { id: nanoid(), role: 'assistant' as Role, content: '', tools: [] },
        };
      },
    },
    // 追加增量 token 到指定 assistant 消息
    appendToken(state, action: PayloadAction<{ id: string; token: string }>) {
      const msg = state.messages.find((m) => m.id === action.payload.id);
      if (msg) msg.content += action.payload.token;
    },
    // 记录一次工具调用
    addToolCall(state, action: PayloadAction<{ id: string; tool: string }>) {
      const msg = state.messages.find((m) => m.id === action.payload.id);
      if (msg) {
        msg.tools = msg.tools ?? [];
        if (!msg.tools.includes(action.payload.tool)) msg.tools.push(action.payload.tool);
      }
    },
    // 流式结束
    finishStreaming(state) {
      state.streaming = false;
    },
    setError(state, action: PayloadAction<string>) {
      state.error = action.payload;
      state.streaming = false;
    },
    clearChat(state) {
      state.messages = [];
      state.error = null;
      state.streaming = false;
    },
  },
});

export const {
  addUserMessage,
  startAssistantMessage,
  appendToken,
  addToolCall,
  finishStreaming,
  setError,
  clearChat,
} = chatSlice.actions;

export default chatSlice.reducer;
