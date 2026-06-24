import { useState } from 'react';
import { Button, Card, Form, Input, Select, Space, Steps, Tag, Typography } from 'antd';
import { ThunderboltOutlined } from '@ant-design/icons';
import { useAppDispatch, useAppSelector } from '../store/hooks';
import {
  addStep,
  appendReportToken,
  finishAnalysis,
  setAnalysisError,
  startAnalysis,
} from '../store/incidentSlice';
import { analyzeAlert, type AlertInput } from '../api/incident';

const { Paragraph, Text } = Typography;

const SAMPLE: AlertInput = {
  alertname: 'HighMemoryUsage',
  service: 'order-service',
  severity: 'critical',
  description: 'order-service 内存使用率超过 95%，P99 延迟飙升，出现 5xx',
};

export default function IncidentPage() {
  const dispatch = useAppDispatch();
  const { steps, report, running, error } = useAppSelector((s) => s.incident);
  const [form] = Form.useForm<AlertInput>();
  const [hasRun, setHasRun] = useState(false);

  const handleRun = async () => {
    const alert = await form.validateFields();
    setHasRun(true);
    dispatch(startAnalysis());
    await analyzeAlert(alert, {
      onStep: (s) => dispatch(addStep(s)),
      onToken: (t) => dispatch(appendReportToken(t)),
      onDone: () => dispatch(finishAnalysis()),
      onError: (m) => dispatch(setAnalysisError(m)),
    });
  };

  return (
    <div style={{ display: 'flex', gap: 16, height: '100%', paddingTop: 16 }}>
      {/* 左：告警输入 + 步骤进度 */}
      <Card title="告警输入" style={{ width: 380, overflow: 'auto' }}>
        <Form form={form} layout="vertical" initialValues={SAMPLE}>
          <Form.Item name="alertname" label="告警名" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="service" label="服务">
            <Input />
          </Form.Item>
          <Form.Item name="severity" label="级别">
            <Select
              options={[
                { value: 'critical', label: 'critical' },
                { value: 'warning', label: 'warning' },
                { value: 'info', label: 'info' },
              ]}
            />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea autoSize={{ minRows: 2, maxRows: 4 }} />
          </Form.Item>
          <Button
            type="primary"
            icon={<ThunderboltOutlined />}
            loading={running}
            onClick={handleRun}
            block
          >
            开始根因分析
          </Button>
        </Form>

        {steps.length > 0 && (
          <div style={{ marginTop: 20 }}>
            <Text strong>分析进度</Text>
            <Steps
              direction="vertical"
              size="small"
              style={{ marginTop: 12 }}
              current={running ? steps.length : steps.length}
              items={steps.map((s) => ({
                title: s.label,
                description: s.detail || undefined,
              }))}
            />
          </div>
        )}
      </Card>

      {/* 右：根因报告 */}
      <Card
        title="根因报告"
        style={{ flex: 1, display: 'flex', flexDirection: 'column' }}
        styles={{ body: { flex: 1, overflow: 'auto' } }}
        extra={
          <Space>
            <Tag color="purple">并行拉取</Tag>
            <Tag color="cyan">假设验证循环</Tag>
          </Space>
        }
      >
        {error && <Text type="danger">出错了：{error}</Text>}
        {report ? (
          <Paragraph style={{ whiteSpace: 'pre-wrap' }}>{report}</Paragraph>
        ) : (
          <Text type="secondary">
            {running
              ? '正在并行拉取日志/指标/发布，生成并验证假设…'
              : hasRun
                ? '等待结论…'
                : '填写告警信息（或用预置示例）后点「开始根因分析」。未配置监控时会用 mock 数据跑通完整链路。'}
          </Text>
        )}
      </Card>
    </div>
  );
}
