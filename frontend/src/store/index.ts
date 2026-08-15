// Redux 根 Store：聚合各业务页面的“客户端交互状态”，例如流式 token、步骤和来源。
// 服务端缓存型状态优先交给 TanStack Query，避免同一数据出现两套缓存真相。
import { configureStore } from '@reduxjs/toolkit';
import chatReducer from './chatSlice';
import codebaseReducer from './codebaseSlice';
import incidentReducer from './incidentSlice';
import knowledgeReducer from './knowledgeSlice';
import requirementReducer from './requirementSlice';
import reviewReducer from './reviewSlice';

// Redux Toolkit 全局 store（客户端状态）
export const store = configureStore({
  reducer: {
    chat: chatReducer,
    knowledge: knowledgeReducer,
    review: reviewReducer,
    incident: incidentReducer,
    codebase: codebaseReducer,
    requirement: requirementReducer,
  },
});

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;
