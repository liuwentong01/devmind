"""GitHub 与监控告警 Webhook：认证/验签、严格解析、去重入队。

调用链：外部系统请求 ``POST /api/webhooks/github`` 或 ``/alert``；路由先读取受限原始 body、
验证 GitHub HMAC 或 Alert Bearer secret，再把最小化载荷以稳定 job id 放入 ARQ，快速返回
202。GitHub 本地签名示意：``sig=sha256=$(printf %s "$body" | openssl dgst -sha256
-hmac "$GITHUB_WEBHOOK_SECRET" | awk '{print $2}')``，请求必须用完全相同的 ``$body`` 字节。

生产边界：Webhook 不叠加浏览器 API key，而使用协议专属 secret；非本地缺少 secret 会在
Settings 阶段拒绝启动。校验签名必须先于 JSON 解析，响应和日志不回显签名、正文或告警描述。
202 只表示队列接受/识别重复，不表示审查或 RCA 已成功；需要队列监控、重试和失败重放机制。
"""

import json
import re
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.security import (
    validate_github_repository,
    verify_bearer_secret,
    verify_hmac_sha256,
)
from app.kernel.queue import get_queue, make_job_id, review_job_id
from app.schemas.incident import AlertInput

logger = get_logger("gateway.webhook")
router = APIRouter(prefix="/webhooks", tags=["webhook"])

# 触发审查的 PR action
TRIGGER_ACTIONS = {"opened", "synchronize", "reopened"}
_DELIVERY_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,128}$")


def _verify_signature(body: bytes, signature: str | None, secret: str) -> bool:
    """校验 GitHub 的 X-Hub-Signature-256 (HMAC-SHA256)。"""
    return verify_hmac_sha256(body, signature, secret)


async def _read_body(request: Request, settings: Settings) -> bytes:
    """检查 JSON media type 并读取受限原始 body，供 HMAC 验签后再解码。

    全局中间件限制全部 HTTP body，本函数再应用更小的 Webhook 专属上限，形成纵深防御。
    """
    content_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    if content_type != "application/json" and not content_type.endswith("+json"):
        raise HTTPException(status_code=415, detail="仅支持 application/json")

    body = await request.body()
    if len(body) > settings.max_webhook_body_bytes:
        raise HTTPException(status_code=413, detail="Webhook 请求体过大")
    return body


def _decode_json_object(body: bytes) -> dict[str, Any]:
    """把已认证字节严格解析为 JSON object，不回传解码器内部细节。"""
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="JSON 请求体无效") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook payload 必须是对象")
    return payload


def _allow_unsigned_local(settings: Settings, webhook: str) -> bool:
    """仅在显式允许的本地/测试环境接受无签名事件，并留下醒目审计日志。"""
    if not settings.unsigned_webhooks_allowed:
        return False
    logger.warning("unsigned_webhook_accepted", webhook=webhook, env=settings.app_env)
    return True


@router.post("/github", status_code=202)
async def github_webhook(
    request: Request,
    x_github_event: str | None = Header(default=None),
    x_hub_signature_256: str | None = Header(default=None),
    x_github_delivery: str | None = Header(default=None),
) -> dict:
    """接收 GitHub PR Webhook；仅支持指定 PR action，并按 head/delivery 去重入队。"""
    settings = get_settings()
    body = await _read_body(request, settings)

    # 必须对原始 body 先验签再解析/使用字段。若先 json.loads 后重新序列化，空白、Unicode 与
    # 键顺序变化都会导致摘要不一致，也可能在身份未确认前消耗更多解析资源。
    if settings.github_webhook_secret:
        signature_valid = _verify_signature(
            body, x_hub_signature_256, settings.github_webhook_secret
        )
    else:
        signature_valid = _allow_unsigned_local(settings, "github")
    if not signature_valid:
        raise HTTPException(status_code=401, detail="签名校验失败")
    payload = _decode_json_object(body)

    if not x_github_delivery:
        if not settings.is_local:
            raise HTTPException(status_code=400, detail="缺少 GitHub delivery id")
    elif not _DELIVERY_ID_RE.fullmatch(x_github_delivery):
        raise HTTPException(status_code=400, detail="GitHub delivery id 无效")

    # 对已正确验签但不关心的事件返回 202 ignored，避免 GitHub 把配置中的其它事件当失败重试。
    if x_github_event != "pull_request":
        return {"status": "ignored", "reason": "unsupported_event"}

    action = payload.get("action")
    if action not in TRIGGER_ACTIONS:
        return {"status": "ignored", "reason": "unsupported_action"}

    pr = payload.get("pull_request", {})
    repo = payload.get("repository", {})
    owner = repo.get("owner", {}).get("login")
    repo_name = repo.get("name")
    number = pr.get("number")
    head_sha = pr.get("head", {}).get("sha") if isinstance(pr, dict) else None
    if not isinstance(head_sha, str) or not re.fullmatch(r"[0-9A-Fa-f]{40,64}", head_sha):
        head_sha = None

    if (
        not isinstance(owner, str)
        or not isinstance(repo_name, str)
        or not validate_github_repository(owner, repo_name)
        or isinstance(number, bool)
        or not isinstance(number, int)
        or not 0 < number <= 2_147_483_647
    ):
        raise HTTPException(status_code=400, detail="payload 缺少 owner/repo/number")

    try:
        queue = await get_queue()
        # head SHA 是最佳业务幂等键：PR 每次更新应重新审查，同一版本无论由 webhook 还是手动
        # 入口触发都只运行一次。旧 payload 无 SHA 时才退回 delivery/action 级去重。
        if head_sha:
            job_id = review_job_id(owner, repo_name, number, head_sha)
        else:
            dedupe_key = x_github_delivery or f"{action}:{number}"
            job_id = make_job_id("review", owner, repo_name, number, dedupe_key)
        job = await queue.enqueue_job(
            "review_pull_task",
            owner,
            repo_name,
            number,
            head_sha,
            _job_id=job_id,
            _expires=86_400,
        )
    except Exception:  # noqa: BLE001
        logger.exception("github_webhook_enqueue_failed", delivery_id=x_github_delivery)
        raise HTTPException(status_code=503, detail="任务队列暂不可用") from None
    logger.info(
        "webhook_enqueued",
        owner=owner,
        repo=repo_name,
        number=number,
        duplicate=job is None,
    )
    return {
        "status": "queued" if job else "duplicate",
        "job_id": job.job_id if job else job_id,
    }


@router.post("/alert", status_code=202)
async def alert_webhook(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict:
    """接收 Alertmanager/单条告警，对每条 firing 告警标准化并去重入队。

    ``resolved`` 告警被跳过；标准 Alertmanager ``fingerprint`` 优先作为去重键，缺失时由规范
    字段生成稳定摘要输入。一次投递数量受配置限制，避免告警风暴瞬间淹没队列。
    """
    settings = get_settings()
    if settings.alert_webhook_secret:
        authorized = verify_bearer_secret(authorization, settings.alert_webhook_secret)
    else:
        authorized = _allow_unsigned_local(settings, "alert")
    if not authorized:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook 身份验证失败",
            headers={"WWW-Authenticate": 'Bearer realm="devmind-alert-webhook"'},
        )
    body = await _read_body(request, settings)
    payload = _decode_json_object(body)

    raw_alerts = payload.get("alerts")
    if raw_alerts is None:
        alerts = [payload]  # 兼容非 Alertmanager 的单条告警
    elif isinstance(raw_alerts, list):
        alerts = raw_alerts
    else:
        raise HTTPException(status_code=400, detail="alerts 必须是数组")
    if len(alerts) > settings.max_alerts_per_webhook:
        raise HTTPException(status_code=413, detail="单次告警数量过多")

    pending: list[tuple[dict, str]] = []
    for raw in alerts:
        if not isinstance(raw, dict):
            raise HTTPException(status_code=400, detail="告警项必须是对象")
        if raw.get("status") == "resolved":
            continue
        labels = raw.get("labels", {})
        annotations = raw.get("annotations", {})
        if not isinstance(labels, dict) or not isinstance(annotations, dict):
            raise HTTPException(status_code=400, detail="告警 labels/annotations 无效")
        try:
            alert = AlertInput(
                alertname=labels.get("alertname") or raw.get("alertname", "未知告警"),
                service=labels.get("service") or labels.get("job", ""),
                severity=labels.get("severity", "warning"),
                description=annotations.get("description")
                or annotations.get("summary")
                or raw.get("description", ""),
                starts_at=raw.get("startsAt", ""),
            ).to_dict()
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail="告警字段无效") from exc
        # Alertmanager fingerprint 最能跨重复投递保持稳定。fallback 不直接作为 Redis job id，
        # make_job_id 会再做哈希，既控制长度也避免把告警描述等敏感文本暴露在队列键中。
        fingerprint = raw.get("fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint or len(fingerprint) > 256:
            fingerprint = "\x1f".join(
                str(alert.get(key, ""))
                for key in ("alertname", "service", "severity", "starts_at", "description")
            )
        pending.append((alert, fingerprint))

    queued = 0
    duplicates = 0
    try:
        queue = await get_queue()
        for alert, fingerprint in pending:
            job_id = make_job_id("alert", fingerprint)
            job = await queue.enqueue_job(
                "analyze_alert_task",
                alert,
                _job_id=job_id,
                _expires=3_600,
            )
            if job is None:
                duplicates += 1
            else:
                queued += 1
    except Exception:  # noqa: BLE001
        logger.exception("alert_webhook_enqueue_failed", attempted=len(pending))
        raise HTTPException(status_code=503, detail="任务队列暂不可用") from None

    logger.info("alert_webhook_enqueued", queued=queued, duplicates=duplicates)
    return {
        "status": "queued",
        "count": len(pending),
        "queued": queued,
        "duplicates": duplicates,
    }
