// P2 手动审查页面：粘贴 unified diff 后展示规则命中数和流式 Review。
// 生产 GitHub Webhook 路径由后端 worker 执行，不依赖本页面保持打开。
import { Alert, Button, Card, Input, Tag, Typography } from 'antd';
import { CodeOutlined } from '@ant-design/icons';
import { useAppDispatch, useAppSelector } from '../store/hooks';
import {
  appendReviewToken,
  finishReview,
  setDiff,
  setReviewError,
  setRulesUsed,
  startReview,
} from '../store/reviewSlice';
import { reviewDiff } from '../api/review';

const { Paragraph, Text } = Typography;

const SAMPLE_DIFF = `diff --git a/user.py b/user.py
@@ -1,5 +1,8 @@
-def get_user(uid):
-    return db.query("SELECT * FROM users WHERE id = " + uid)
+def get_user(uid):
+    sql = "SELECT * FROM users WHERE id = %s"
+    return db.query(sql, (uid,))`;

export default function ReviewPage() {
  const dispatch = useAppDispatch();
  const { diff, review, rulesUsed, reviewing, error } = useAppSelector((s) => s.review);

  const handleReview = async () => {
    if (!diff.trim() || reviewing) return;
    dispatch(startReview());
    await reviewDiff(diff, {
      onRules: (count) => dispatch(setRulesUsed(count)),
      onToken: (t) => dispatch(appendReviewToken(t)),
      onDone: () => dispatch(finishReview()),
      onError: (m) => dispatch(setReviewError(m)),
    });
  };

  return (
    <div style={{ display: 'flex', gap: 16, height: '100%', paddingTop: 16 }}>
      {/* 左：diff 输入 */}
      <Card
        title="待审查 diff"
        style={{ width: '45%', display: 'flex', flexDirection: 'column' }}
        styles={{ body: { flex: 1, display: 'flex', flexDirection: 'column' } }}
        extra={
          <Button size="small" onClick={() => dispatch(setDiff(SAMPLE_DIFF))} disabled={reviewing}>
            填入示例
          </Button>
        }
      >
        <Input.TextArea
          value={diff}
          onChange={(e) => dispatch(setDiff(e.target.value))}
          placeholder="粘贴 git diff（git diff / PR diff）"
          style={{ flex: 1, fontFamily: 'monospace', fontSize: 12 }}
          autoSize={false}
          disabled={reviewing}
        />
        <Button
          type="primary"
          icon={<CodeOutlined />}
          loading={reviewing}
          onClick={handleReview}
          style={{ marginTop: 12 }}
          block
        >
          开始审查
        </Button>
      </Card>

      {/* 右：审查意见 */}
      <Card
        title="审查意见"
        style={{ flex: 1, display: 'flex', flexDirection: 'column' }}
        styles={{ body: { flex: 1, overflow: 'auto' } }}
        extra={rulesUsed > 0 ? <Tag color="blue">参考 {rulesUsed} 条团队规范</Tag> : null}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="团队审查规范来自知识库（P1）"
          description="在「知识答疑」页入库 source 为 review-rule 的规范文档，审查时会自动检索并作为依据。"
        />
        {error && <Text type="danger">出错了：{error}</Text>}
        {(review || reviewing) && (
          <Paragraph style={{ whiteSpace: 'pre-wrap' }}>
            {review || '检索规范并审查中…'}
          </Paragraph>
        )}
        {!review && !reviewing && !error && (
          <Text type="secondary">左侧粘贴 diff 后点「开始审查」，可先点「填入示例」体验。</Text>
        )}
      </Card>
    </div>
  );
}
