// 应用壳：集中声明侧边导航、按路由懒加载页面，并隐藏仅供本地运维演示的成本页。
// 使用入口：main.tsx 在 BrowserRouter 中渲染本组件；新增页面时同时更新菜单与 Routes。
import { lazy, Suspense } from 'react';
import { Layout, Menu, Spin, Typography } from 'antd';
import {
  ApartmentOutlined,
  CodeOutlined,
  DollarOutlined,
  MessageOutlined,
  ProfileOutlined,
  ReadOutlined,
  RobotOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';

const ChatPage = lazy(() => import('./pages/ChatPage'));
const CodebasePage = lazy(() => import('./pages/CodebasePage'));
const CostPage = lazy(() => import('./pages/CostPage'));
const IncidentPage = lazy(() => import('./pages/IncidentPage'));
const KnowledgePage = lazy(() => import('./pages/KnowledgePage'));
const RequirementPage = lazy(() => import('./pages/RequirementPage'));
const ReviewPage = lazy(() => import('./pages/ReviewPage'));

const { Header, Sider, Content } = Layout;

const MENU_ITEMS = [
  { key: '/knowledge', icon: <ReadOutlined />, label: '知识答疑 (P1)' },
  { key: '/review', icon: <CodeOutlined />, label: '代码审查 (P2)' },
  { key: '/incident', icon: <ThunderboltOutlined />, label: '告警根因 (P3)' },
  { key: '/requirement', icon: <ProfileOutlined />, label: '需求拆解 (P4)' },
  { key: '/codebase', icon: <ApartmentOutlined />, label: '代码理解 (P5)' },
  ...(import.meta.env.DEV
    ? [{ key: '/cost', icon: <DollarOutlined />, label: '成本治理 (P6，本地)' }]
    : []),
  { key: '/chat', icon: <MessageOutlined />, label: '通用对话 (P0)' },
];

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();

  return (
    <Layout style={{ height: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center', background: '#001529' }}>
        <RobotOutlined style={{ color: '#1677ff', fontSize: 22, marginRight: 10 }} />
        <Typography.Title level={4} style={{ color: '#fff', margin: 0 }}>
          DevMind · 研发效能智能体平台
        </Typography.Title>
      </Header>
      <Layout>
        <Sider width={220} theme="light">
          <Menu
            mode="inline"
            selectedKeys={[location.pathname]}
            items={MENU_ITEMS}
            onClick={({ key }) => navigate(key)}
            style={{ height: '100%', borderRight: 0 }}
          />
        </Sider>
        <Content style={{ padding: '0 24px 24px', overflow: 'hidden' }}>
          <div style={{ maxWidth: 1100, margin: '0 auto', height: '100%' }}>
            <Suspense
              fallback={
                <div style={{ display: 'grid', height: '100%', placeItems: 'center' }}>
                  <Spin size="large" />
                </div>
              }
            >
              <Routes>
                <Route path="/" element={<Navigate to="/knowledge" replace />} />
                <Route path="/knowledge" element={<KnowledgePage />} />
                <Route path="/review" element={<ReviewPage />} />
                <Route path="/incident" element={<IncidentPage />} />
                <Route path="/requirement" element={<RequirementPage />} />
                <Route path="/codebase" element={<CodebasePage />} />
                {import.meta.env.DEV && <Route path="/cost" element={<CostPage />} />}
                <Route path="/chat" element={<ChatPage />} />
                <Route path="*" element={<Navigate to="/knowledge" replace />} />
              </Routes>
            </Suspense>
          </div>
        </Content>
      </Layout>
    </Layout>
  );
}
