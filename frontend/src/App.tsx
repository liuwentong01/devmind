import { Layout, Menu, Typography } from 'antd';
import { CodeOutlined, MessageOutlined, ReadOutlined, RobotOutlined } from '@ant-design/icons';
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import ChatPage from './pages/ChatPage';
import KnowledgePage from './pages/KnowledgePage';
import ReviewPage from './pages/ReviewPage';

const { Header, Sider, Content } = Layout;

const MENU_ITEMS = [
  { key: '/knowledge', icon: <ReadOutlined />, label: '知识答疑 (P1)' },
  { key: '/review', icon: <CodeOutlined />, label: '代码审查 (P2)' },
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
            <Routes>
              <Route path="/" element={<Navigate to="/knowledge" replace />} />
              <Route path="/knowledge" element={<KnowledgePage />} />
              <Route path="/review" element={<ReviewPage />} />
              <Route path="/chat" element={<ChatPage />} />
            </Routes>
          </div>
        </Content>
      </Layout>
    </Layout>
  );
}
