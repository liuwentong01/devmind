import { useState } from 'react';
import {
  Button,
  Card,
  Input,
  List,
  Space,
  Statistic,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd';
import { ApiOutlined, BranchesOutlined, SearchOutlined } from '@ant-design/icons';
import { useAppDispatch, useAppSelector } from '../store/hooks';
import {
  appendAnswerToken,
  finishQuery,
  setContextFiles,
  setError,
  setImpact,
  setImpactLoading,
  setIndexing,
  setRepo,
  setStats,
  startQuery,
} from '../store/codebaseSlice';
import { analyzeImpact, indexRepo, queryCodebase } from '../api/codebase';

const { Paragraph, Text } = Typography;

export default function CodebasePage() {
  const dispatch = useAppDispatch();
  const { repo, stats, indexing, contextFiles, answer, querying, impact, impactLoading, error } =
    useAppSelector((s) => s.codebase);

  const [path, setPath] = useState('');
  const [question, setQuestion] = useState('');
  const [func, setFunc] = useState('');

  const handleIndex = async () => {
    if (!repo.trim() || !path.trim()) {
      message.warning('仓库名和本地路径都要填');
      return;
    }
    dispatch(setIndexing(true));
    try {
      const s = await indexRepo(repo.trim(), path.trim());
      dispatch(setStats(s));
      message.success(`索引完成：${s.functions} 个函数 / ${s.calls} 条调用`);
    } catch (e) {
      dispatch(setError((e as Error).message));
      message.error((e as Error).message);
    } finally {
      dispatch(setIndexing(false));
    }
  };

  const handleQuery = async () => {
    if (!repo.trim() || !question.trim() || querying) return;
    dispatch(startQuery());
    await queryCodebase(repo.trim(), question, {
      onContext: (files) => dispatch(setContextFiles(files)),
      onToken: (t) => dispatch(appendAnswerToken(t)),
      onDone: () => dispatch(finishQuery()),
      onError: (m) => dispatch(setError(m)),
    });
  };

  const handleImpact = async () => {
    if (!repo.trim() || !func.trim()) {
      message.warning('填写仓库名和目标函数名');
      return;
    }
    dispatch(setImpactLoading(true));
    try {
      dispatch(setImpact(await analyzeImpact(repo.trim(), func.trim())));
    } catch (e) {
      dispatch(setError((e as Error).message));
      message.error((e as Error).message);
    } finally {
      dispatch(setImpactLoading(false));
    }
  };

  return (
    <div style={{ display: 'flex', gap: 16, height: '100%', paddingTop: 16 }}>
      {/* 左：索引 */}
      <Card title="索引仓库" style={{ width: 360, overflow: 'auto' }}>
        <Space direction="vertical" style={{ width: '100%' }}>
          <Input
            placeholder="仓库逻辑名，如 devmind"
            value={repo}
            onChange={(e) => dispatch(setRepo(e.target.value))}
          />
          <Input
            placeholder="本地绝对路径，如 /Users/me/Code/devmind/backend"
            value={path}
            onChange={(e) => setPath(e.target.value)}
          />
          <Button
            type="primary"
            icon={<BranchesOutlined />}
            block
            loading={indexing}
            onClick={handleIndex}
          >
            构建代码图谱
          </Button>
        </Space>

        {stats && (
          <Space style={{ marginTop: 20, width: '100%', justifyContent: 'space-around' }}>
            <Statistic title="文件" value={stats.files} />
            <Statistic title="函数" value={stats.functions} />
            <Statistic title="调用" value={stats.calls} />
          </Space>
        )}
        {error && (
          <Text type="danger" style={{ display: 'block', marginTop: 12 }}>
            {error}
          </Text>
        )}
      </Card>

      {/* 右：问答 / 影响分析 */}
      <Card style={{ flex: 1, display: 'flex', flexDirection: 'column' }} styles={{ body: { flex: 1, overflow: 'auto' } }}>
        <Tabs
          items={[
            {
              key: 'qa',
              label: (
                <span>
                  <SearchOutlined /> 代码问答
                </span>
              ),
              children: (
                <div>
                  <Space.Compact style={{ width: '100%', marginBottom: 12 }}>
                    <Input
                      placeholder="例：index_repo 这个函数做了什么？"
                      value={question}
                      onChange={(e) => setQuestion(e.target.value)}
                      onPressEnter={handleQuery}
                      disabled={querying}
                    />
                    <Button type="primary" loading={querying} onClick={handleQuery}>
                      提问
                    </Button>
                  </Space.Compact>
                  {contextFiles.length > 0 && (
                    <div style={{ marginBottom: 12 }}>
                      <Text type="secondary">参考文件：</Text>
                      {contextFiles.map((f) => (
                        <Tag key={f}>{f}</Tag>
                      ))}
                    </div>
                  )}
                  {(answer || querying) && (
                    <Paragraph style={{ whiteSpace: 'pre-wrap' }}>
                      {answer || '检索图谱并回答中…'}
                    </Paragraph>
                  )}
                </div>
              ),
            },
            {
              key: 'impact',
              label: (
                <span>
                  <ApiOutlined /> 影响分析
                </span>
              ),
              children: (
                <div>
                  <Space.Compact style={{ width: '100%', marginBottom: 12 }}>
                    <Input
                      placeholder="目标函数名，如 retrieve"
                      value={func}
                      onChange={(e) => setFunc(e.target.value)}
                      onPressEnter={handleImpact}
                    />
                    <Button type="primary" loading={impactLoading} onClick={handleImpact}>
                      分析
                    </Button>
                  </Space.Compact>
                  {impact && (
                    <Space direction="vertical" style={{ width: '100%' }}>
                      <Paragraph style={{ whiteSpace: 'pre-wrap' }}>{impact.summary}</Paragraph>
                      <Text strong>调用者（影响面 {impact.callers.length}）</Text>
                      <List
                        size="small"
                        dataSource={impact.callers}
                        locale={{ emptyText: '无' }}
                        renderItem={(c) => (
                          <List.Item>
                            <Text code>{c.name}</Text>
                            <Text type="secondary">{c.path}</Text>
                          </List.Item>
                        )}
                      />
                    </Space>
                  )}
                </div>
              ),
            },
          ]}
        />
      </Card>
    </div>
  );
}
