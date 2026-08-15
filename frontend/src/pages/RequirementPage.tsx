// P4 两阶段 HITL 页面：先 plan、允许人工编辑，再 confirm 触发 Jira 写入。
// thread_id 是两次请求的关联键；页面不得自行伪造“已完成”状态。
import { useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Input,
  Result,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import { DeleteOutlined } from '@ant-design/icons';
import { useAppDispatch, useAppSelector } from '../store/hooks';
import {
  removeTask,
  reset,
  setConfirming,
  setError,
  setPlan,
  setResult,
  startPlan,
  updateTask,
} from '../store/requirementSlice';
import { confirmRequirement, planRequirement, type TaskItem } from '../api/requirement';

const { Text } = Typography;

const SAMPLE_PRD = `需求：用户积分商城
1. 用户可浏览可兑换商品列表（分页、按积分排序）
2. 用户用积分兑换商品，扣减积分并生成兑换订单
3. 运营后台可配置商品（上下架、库存、所需积分）
4. 兑换记录可查询与导出`;

export default function RequirementPage() {
  const dispatch = useAppDispatch();
  const { threadId, tasks, planning, confirming, result, error } = useAppSelector(
    (s) => s.requirement,
  );
  const [prd, setPrd] = useState(SAMPLE_PRD);
  const [project, setProject] = useState('');
  const [epic, setEpic] = useState('');

  const handlePlan = async () => {
    if (!prd.trim()) {
      message.warning('请填写 PRD 内容');
      return;
    }
    dispatch(startPlan());
    try {
      const r = await planRequirement({ prd_text: prd, project, epic });
      dispatch(setPlan({ threadId: r.thread_id, tasks: r.tasks }));
    } catch (e) {
      dispatch(setError((e as Error).message));
      message.error((e as Error).message);
    }
  };

  const handleConfirm = async () => {
    if (!tasks.length) return;
    dispatch(setConfirming(true));
    try {
      dispatch(setResult(await confirmRequirement(threadId, tasks)));
    } catch (e) {
      dispatch(setError((e as Error).message));
      message.error((e as Error).message);
    }
  };

  const columns = [
    {
      title: '标题',
      dataIndex: 'title',
      render: (v: string, _r: TaskItem, i: number) => (
        <Input
          value={v}
          onChange={(e) => dispatch(updateTask({ index: i, patch: { title: e.target.value } }))}
        />
      ),
    },
    {
      title: '类型',
      dataIndex: 'type',
      width: 110,
      render: (v: TaskItem['type'], _r: TaskItem, i: number) => (
        <Select<TaskItem['type']>
          value={v}
          style={{ width: 100 }}
          onChange={(val) => dispatch(updateTask({ index: i, patch: { type: val } }))}
          options={[
            { value: 'backend', label: '后端' },
            { value: 'frontend', label: '前端' },
            { value: 'task', label: '通用任务' },
          ]}
        />
      ),
    },
    {
      title: '点数',
      dataIndex: 'story_points',
      width: 90,
      render: (v: TaskItem['story_points'], _r: TaskItem, i: number) => (
        <Select
          value={v ?? undefined}
          allowClear
          placeholder="未估"
          style={{ width: 76 }}
          options={[1, 2, 3, 5, 8].map((point) => ({ value: point, label: point }))}
          onChange={(val) =>
            dispatch(
              updateTask({
                index: i,
                patch: { story_points: (val as TaskItem['story_points']) ?? null },
              }),
            )
          }
        />
      ),
    },
    {
      title: '操作',
      width: 60,
      render: (_v: unknown, _r: TaskItem, i: number) => (
        <Button type="text" danger icon={<DeleteOutlined />} onClick={() => dispatch(removeTask(i))} />
      ),
    },
  ];

  if (result) {
    return (
      <div style={{ paddingTop: 24 }}>
        <Result
          status="success"
          title={result.dry_run ? '已生成任务（dry-run，未真正写入 Jira）' : '已写入 Jira'}
          subTitle={`项目 ${result.project}${result.epic ? ` / Epic ${result.epic}` : ''}，共 ${result.created.length} 个任务`}
          extra={[
            <Button type="primary" key="again" onClick={() => dispatch(reset())}>
              再拆一个
            </Button>,
          ]}
        >
          <Space direction="vertical">
            {result.created.map((c) => (
              <Text key={c.key}>
                <Tag color="blue">{c.key}</Tag> {c.title}
              </Text>
            ))}
          </Space>
        </Result>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', gap: 16, height: '100%', paddingTop: 16 }}>
      {/* 左：PRD 输入 */}
      <Card title="PRD 输入" style={{ width: 380, overflow: 'auto' }}>
        <Space direction="vertical" style={{ width: '100%' }}>
          <Input.TextArea
            value={prd}
            onChange={(e) => setPrd(e.target.value)}
            autoSize={{ minRows: 8, maxRows: 16 }}
            placeholder="粘贴 PRD 正文（也可在后端配置 Atlassian 后用 Confluence 链接）"
          />
          <Space.Compact style={{ width: '100%' }}>
            <Input placeholder="Jira 项目 key（如 DEV）" value={project} onChange={(e) => setProject(e.target.value)} />
            <Input placeholder="Epic key（可选）" value={epic} onChange={(e) => setEpic(e.target.value)} />
          </Space.Compact>
          <Button type="primary" block loading={planning} onClick={handlePlan}>
            拆解任务
          </Button>
        </Space>
      </Card>

      {/* 右：HITL 人工确认 */}
      <Card
        title="任务提议（人工确认闸门）"
        style={{ flex: 1, display: 'flex', flexDirection: 'column' }}
        styles={{ body: { flex: 1, overflow: 'auto' } }}
        extra={
          tasks.length > 0 ? (
            <Button type="primary" loading={confirming} onClick={handleConfirm}>
              确认并写入 Jira
            </Button>
          ) : null
        }
      >
        {error && <Text type="danger">出错了：{error}</Text>}
        {tasks.length > 0 ? (
          <>
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 12 }}
              message="HITL 人工闸门"
              description="以下任务由 Agent 提议，尚未写入 Jira。你可编辑标题/类型/点数或删除，确认后才会真正创建。"
            />
            <Table
              rowKey={(_r, i) => String(i)}
              size="small"
              pagination={false}
              columns={columns}
              dataSource={tasks}
            />
          </>
        ) : (
          !error && <Text type="secondary">左侧填 PRD 后点「拆解任务」，Agent 会提议任务清单等你确认。</Text>
        )}
      </Card>
    </div>
  );
}
