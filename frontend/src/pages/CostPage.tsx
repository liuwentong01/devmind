// P6 本地成本面板：TanStack Query 定时读取快照，并通过 mutation 执行 reset。
// App.tsx 仅在 DEV 构建暴露本页；生产管理仍必须走独立、受审计的 admin 通道。
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Space, Statistic, Table, Tag, Typography } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { fetchCost, resetCost, type ModelUsage } from '../api/cost';

const { Text } = Typography;

// P6：成本治理面板，用 TanStack Query 管理服务端状态（轮询 + 失效刷新）
export default function CostPage() {
  const qc = useQueryClient();
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['cost'],
    queryFn: fetchCost,
    refetchInterval: 5000, // 每 5s 自动刷新
  });

  const resetMutation = useMutation({
    mutationFn: resetCost,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['cost'] }),
  });

  const rows = data
    ? Object.entries(data.by_model).map(([model, u]) => ({ model, ...u }))
    : [];

  const columns = [
    { title: '模型', dataIndex: 'model', render: (v: string) => <Tag color="blue">{v}</Tag> },
    { title: '请求数', dataIndex: 'requests', width: 90 },
    { title: 'Prompt tokens', dataIndex: 'prompt_tokens', width: 130 },
    { title: 'Completion tokens', dataIndex: 'completion_tokens', width: 150 },
    { title: '总 tokens', dataIndex: 'total_tokens', width: 110 },
    {
      title: '费用 (USD)',
      dataIndex: 'cost_usd',
      width: 110,
      render: (v: number) => `$${v.toFixed(6)}`,
    },
  ];

  return (
    <div style={{ paddingTop: 16 }}>
      <Card
        title="LLM 成本治理"
        extra={
          <Button
            icon={<ReloadOutlined />}
            loading={resetMutation.isPending}
            onClick={() => resetMutation.mutate()}
          >
            重置计数
          </Button>
        }
      >
        {data?.degraded && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 16 }}
            message="成本统计已降级为单实例内存存储"
            description="Redis 暂不可用；当前数据不会跨 API/worker 副本聚合，恢复 Redis 后请复核成本报表。"
          />
        )}
        {data?.storage === 'disabled' && (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
            message="成本统计已关闭"
          />
        )}
        {resetMutation.isError && (
          <Alert
            type="error"
            showIcon
            style={{ marginBottom: 16 }}
            message="重置失败"
            description={(resetMutation.error as Error).message}
          />
        )}
        {isError ? (
          <Text type="danger">{(error as Error).message}</Text>
        ) : (
          <>
            <Space size="large" style={{ marginBottom: 24 }}>
              <Statistic title="总请求数" value={data?.total_requests ?? 0} loading={isLoading} />
              <Statistic
                title="累计费用 (USD)"
                value={data?.total_cost_usd ?? 0}
                precision={6}
                prefix="$"
                loading={isLoading}
              />
              <Statistic title="模型数" value={rows.length} loading={isLoading} />
            </Space>
            <Table
              rowKey="model"
              size="small"
              loading={isLoading}
              pagination={false}
              columns={columns}
              dataSource={rows as (ModelUsage & { model: string })[]}
              locale={{ emptyText: '暂无调用记录，先去各 Agent 页面跑几次' }}
            />
            <Text type="secondary" style={{ display: 'block', marginTop: 12 }}>
              注：未收录在锁定价格表中的模型可能显示 $0，token 仍会统计；正式结算以供应商账单为准。每 5 秒自动刷新。
            </Text>
          </>
        )}
      </Card>
    </div>
  );
}
