// React 渲染错误兜底：防止某个页面异常后只留下白屏。
// 它不捕获所有异步请求错误；API/SSE 错误仍由各 slice/page 显式处理。
import { Component, type ErrorInfo, type ReactNode } from 'react';
import { Button, Result } from 'antd';

interface Props {
  children: ReactNode;
}

interface State {
  failed: boolean;
}

export default class AppErrorBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // A production error collector can hook console.error without exposing details to users.
    console.error('Uncaught application error', error, info.componentStack);
  }

  render() {
    if (this.state.failed) {
      return (
        <Result
          status="500"
          title="页面暂时不可用"
          subTitle="客户端发生了未处理错误，请刷新后重试。"
          extra={<Button onClick={() => window.location.reload()}>刷新页面</Button>}
        />
      );
    }
    return this.props.children;
  }
}
