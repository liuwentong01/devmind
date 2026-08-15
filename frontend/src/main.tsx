// 浏览器启动入口：按 Redux → TanStack Query → Ant Design → ErrorBoundary → Router
// 的顺序装配全局能力。通常无需业务代码直接调用，Vite 会从 index.html 加载本文件。
import React from 'react';
import ReactDOM from 'react-dom/client';
import { Provider } from 'react-redux';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { BrowserRouter } from 'react-router-dom';
import { store } from './store';
import App from './App';
import AppErrorBoundary from './components/AppErrorBoundary';

// TanStack Query 负责服务端状态；当前 P6 成本面板用它读取、轮询和重置快照。
const queryClient = new QueryClient();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <Provider store={store}>
      <QueryClientProvider client={queryClient}>
        <ConfigProvider locale={zhCN}>
          <AppErrorBoundary>
            <BrowserRouter>
              <App />
            </BrowserRouter>
          </AppErrorBoundary>
        </ConfigProvider>
      </QueryClientProvider>
    </Provider>
  </React.StrictMode>,
);
