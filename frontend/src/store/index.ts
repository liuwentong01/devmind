import { configureStore } from '@reduxjs/toolkit';
import chatReducer from './chatSlice';
import codebaseReducer from './codebaseSlice';
import incidentReducer from './incidentSlice';
import knowledgeReducer from './knowledgeSlice';
import reviewReducer from './reviewSlice';

// Redux Toolkit 全局 store（客户端状态）
export const store = configureStore({
  reducer: {
    chat: chatReducer,
    knowledge: knowledgeReducer,
    review: reviewReducer,
    incident: incidentReducer,
    codebase: codebaseReducer,
  },
});

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;
