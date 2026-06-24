import { configureStore } from '@reduxjs/toolkit';
import chatReducer from './chatSlice';

// Redux Toolkit 全局 store（客户端状态）
export const store = configureStore({
  reducer: {
    chat: chatReducer,
  },
});

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;
