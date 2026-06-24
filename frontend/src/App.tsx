import { Layout, Typography } from 'antd';
import { RobotOutlined } from '@ant-design/icons';
import ChatPage from './pages/ChatPage';

const { Header, Content } = Layout;

export default function App() {
  return (
    <Layout style={{ height: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center', background: '#001529' }}>
        <RobotOutlined style={{ color: '#1677ff', fontSize: 22, marginRight: 10 }} />
        <Typography.Title level={4} style={{ color: '#fff', margin: 0 }}>
          DevMind · 研发效能智能体平台
        </Typography.Title>
      </Header>
      <Content
        style={{
          maxWidth: 900,
          width: '100%',
          margin: '0 auto',
          padding: '0 24px 24px',
          height: 'calc(100vh - 64px)',
        }}
      >
        <ChatPage />
      </Content>
    </Layout>
  );
}
