import { useCallback, useEffect, useState } from 'react';
import {
  Button,
  Card,
  Empty,
  Input,
  List,
  Popconfirm,
  Space,
  Tag,
  Typography,
  message,
} from 'antd';
import { DeleteOutlined, SendOutlined } from '@ant-design/icons';
import { useAppDispatch, useAppSelector } from '../store/hooks';
import {
  appendAnswerToken,
  finishQuery,
  setDocsLoading,
  setDocuments,
  setQueryError,
  setSources,
  startQuery,
} from '../store/knowledgeSlice';
import {
  deleteDocument,
  ingestDocument,
  listDocuments,
  queryKnowledge,
} from '../api/knowledge';

const { Title, Text, Paragraph } = Typography;

export default function KnowledgePage() {
  const dispatch = useAppDispatch();
  const { documents, docsLoading, answer, sources, querying, error } = useAppSelector(
    (s) => s.knowledge,
  );

  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [question, setQuestion] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const loadDocs = useCallback(async () => {
    dispatch(setDocsLoading(true));
    try {
      dispatch(setDocuments(await listDocuments()));
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      dispatch(setDocsLoading(false));
    }
  }, [dispatch]);

  useEffect(() => {
    loadDocs();
  }, [loadDocs]);

  const handleIngest = async () => {
    if (!title.trim() || !content.trim()) {
      message.warning('标题和内容都不能为空');
      return;
    }
    setSubmitting(true);
    try {
      await ingestDocument(title.trim(), content.trim());
      message.success('入库成功');
      setTitle('');
      setContent('');
      await loadDocs();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await deleteDocument(id);
      await loadDocs();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const handleAsk = async () => {
    const q = question.trim();
    if (!q || querying) return;
    dispatch(startQuery(q));
    await queryKnowledge(q, {
      onSources: (s) => dispatch(setSources(s)),
      onToken: (t) => dispatch(appendAnswerToken(t)),
      onDone: () => dispatch(finishQuery()),
      onError: (m) => dispatch(setQueryError(m)),
    });
  };

  return (
    <div style={{ display: 'flex', gap: 16, height: '100%', paddingTop: 16 }}>
      {/* 左：文档管理 */}
      <Card title="知识文档" style={{ width: 380, display: 'flex', flexDirection: 'column' }} styles={{ body: { flex: 1, overflow: 'auto' } }}>
        <Space direction="vertical" style={{ width: '100%' }}>
          <Input
            placeholder="文档标题"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <Input.TextArea
            placeholder="粘贴文档正文，入库时会自动分块并向量化"
            value={content}
            onChange={(e) => setContent(e.target.value)}
            autoSize={{ minRows: 4, maxRows: 8 }}
          />
          <Button type="primary" block loading={submitting} onClick={handleIngest}>
            入库
          </Button>
        </Space>

        <List
          style={{ marginTop: 16 }}
          loading={docsLoading}
          dataSource={documents}
          locale={{ emptyText: <Empty description="暂无文档" /> }}
          renderItem={(doc) => (
            <List.Item
              actions={[
                <Popconfirm
                  key="del"
                  title="确认删除该文档？"
                  onConfirm={() => handleDelete(doc.id)}
                >
                  <Button type="text" danger size="small" icon={<DeleteOutlined />} />
                </Popconfirm>,
              ]}
            >
              <List.Item.Meta
                title={doc.title}
                description={<Tag color="blue">{doc.chunk_count} 切片</Tag>}
              />
            </List.Item>
          )}
        />
      </Card>

      {/* 右：RAG 问答 */}
      <Card title="知识问答（RAG）" style={{ flex: 1, display: 'flex', flexDirection: 'column' }} styles={{ body: { flex: 1, overflow: 'auto' } }}>
        <Space.Compact style={{ width: '100%', marginBottom: 16 }}>
          <Input
            placeholder="基于已入库文档提问，回答会标注来源 [n]"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onPressEnter={handleAsk}
            disabled={querying}
          />
          <Button type="primary" icon={<SendOutlined />} loading={querying} onClick={handleAsk}>
            提问
          </Button>
        </Space.Compact>

        {error && <Text type="danger">出错了：{error}</Text>}

        {(answer || querying) && (
          <Card type="inner" title="回答" style={{ marginBottom: 16 }}>
            <Paragraph style={{ whiteSpace: 'pre-wrap', margin: 0 }}>
              {answer || '检索并生成中…'}
            </Paragraph>
          </Card>
        )}

        {sources.length > 0 && (
          <div>
            <Title level={5}>引用来源</Title>
            <List
              size="small"
              dataSource={sources}
              renderItem={(s) => (
                <List.Item>
                  <Space direction="vertical" size={2} style={{ width: '100%' }}>
                    <Space>
                      <Tag color="geekblue">[{s.index}]</Tag>
                      <Text strong>{s.title}</Text>
                      <Tag>相似度 {s.score}</Tag>
                    </Space>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {s.snippet}…
                    </Text>
                  </Space>
                </List.Item>
              )}
            />
          </div>
        )}
      </Card>
    </div>
  );
}
