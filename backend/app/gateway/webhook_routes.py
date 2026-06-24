"""GitHub Webhook 接收器：验签 → 解析 PR 事件 → 入队异步审查。

事件驱动的核心：接收即快速返回 202，真正的审查交给 ARQ worker。
"""

import hashlib
import hmac

from fastapi import APIRouter, Header, HTTPException, Request

from app.core.config import get_settings
from app.core.logging import get_logger
from app.kernel.queue import get_queue

logger = get_logger("gateway.webhook")
router = APIRouter(prefix="/webhooks", tags=["webhook"])

# 触发审查的 PR action
TRIGGER_ACTIONS = {"opened", "synchronize", "reopened"}


def _verify_signature(body: bytes, signature: str | None, secret: str) -> bool:
    """校验 GitHub 的 X-Hub-Signature-256 (HMAC-SHA256)。"""
    if not secret:
        # 未配置密钥时跳过校验（仅本地调试用，生产必须配置）
        return True
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/github", status_code=202)
async def github_webhook(
    request: Request,
    x_github_event: str | None = Header(default=None),
    x_hub_signature_256: str | None = Header(default=None),
) -> dict:
    """接收 GitHub PR Webhook，验签后把审查任务入队。"""
    settings = get_settings()
    body = await request.body()

    if not _verify_signature(body, x_hub_signature_256, settings.github_webhook_secret):
        raise HTTPException(status_code=401, detail="签名校验失败")

    if x_github_event != "pull_request":
        return {"status": "ignored", "reason": f"event={x_github_event}"}

    payload = await request.json()
    action = payload.get("action")
    if action not in TRIGGER_ACTIONS:
        return {"status": "ignored", "reason": f"action={action}"}

    pr = payload.get("pull_request", {})
    repo = payload.get("repository", {})
    owner = repo.get("owner", {}).get("login")
    repo_name = repo.get("name")
    number = pr.get("number")

    if not (owner and repo_name and number):
        raise HTTPException(status_code=400, detail="payload 缺少 owner/repo/number")

    queue = await get_queue()
    job = await queue.enqueue_job("review_pull_task", owner, repo_name, number)
    logger.info("webhook_enqueued", owner=owner, repo=repo_name, number=number)
    return {"status": "queued", "job_id": job.job_id if job else None}


@router.post("/alert", status_code=202)
async def alert_webhook(request: Request) -> dict:
    """接收监控告警（Alertmanager 格式），对每条 firing 告警入队根因分析。"""
    payload = await request.json()
    alerts = payload.get("alerts") or [payload]  # 兼容非 Alertmanager 的单条告警

    queue = await get_queue()
    queued = 0
    for raw in alerts:
        if raw.get("status") == "resolved":
            continue
        labels = raw.get("labels", {})
        annotations = raw.get("annotations", {})
        alert = {
            "alertname": labels.get("alertname") or raw.get("alertname", "未知告警"),
            "service": labels.get("service") or labels.get("job", ""),
            "severity": labels.get("severity", "warning"),
            "description": annotations.get("description")
            or annotations.get("summary")
            or raw.get("description", ""),
            "starts_at": raw.get("startsAt", ""),
        }
        await queue.enqueue_job("analyze_alert_task", alert)
        queued += 1

    logger.info("alert_webhook_enqueued", count=queued)
    return {"status": "queued", "count": queued}
