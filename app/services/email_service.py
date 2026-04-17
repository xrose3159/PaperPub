"""QQ 邮箱 SMTP 验证码发送服务。"""

from __future__ import annotations

import random
import smtplib
import string
import time
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from threading import Lock

from app.core.config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM_NAME

log = logging.getLogger(__name__)

_CODE_TTL = 300  # 5 minutes
_CODE_INTERVAL = 60  # min 60s between sends to same email

_store: dict[str, tuple[str, float]] = {}  # email -> (code, created_ts)
_lock = Lock()


def _gen_code(length: int = 6) -> str:
    return "".join(random.choices(string.digits, k=length))


def send_verification_email(to_email: str, code: str) -> None:
    """Send a verification code email via QQ SMTP (SSL on port 465)."""
    if not SMTP_USER or not SMTP_PASSWORD:
        raise RuntimeError("SMTP_USER / SMTP_PASSWORD 未配置，无法发送邮件")

    msg = MIMEMultipart("alternative")
    msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_USER}>"
    msg["To"] = to_email
    msg["Subject"] = f"【{SMTP_FROM_NAME}】邮箱验证码"

    html = f"""\
<div style="max-width:480px;margin:0 auto;font-family:system-ui,sans-serif;padding:32px;background:#fafafa;border-radius:12px;">
  <h2 style="text-align:center;color:#333;margin-bottom:4px;">PaperPub 邮箱验证</h2>
  <p style="text-align:center;color:#888;font-size:14px;margin-top:0;">您的验证码为：</p>
  <div style="text-align:center;font-size:36px;font-weight:bold;letter-spacing:8px;color:#b8862e;padding:20px 0;">{code}</div>
  <p style="text-align:center;color:#999;font-size:13px;">验证码 {_CODE_TTL // 60} 分钟内有效，请勿泄露给他人。</p>
  <hr style="border:none;border-top:1px solid #eee;margin:24px 0 12px;">
  <p style="text-align:center;color:#bbb;font-size:12px;">如非本人操作，请忽略此邮件。</p>
</div>"""

    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15) as server:
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, [to_email], msg.as_string())

    log.info("验证码邮件已发送至 %s", to_email)


def create_and_send_code(email: str) -> None:
    """Generate a code, store it, and email it. Raises on rate-limit or SMTP failure."""
    email = email.strip().lower()
    now = time.time()

    with _lock:
        if email in _store:
            _, ts = _store[email]
            if now - ts < _CODE_INTERVAL:
                remaining = int(_CODE_INTERVAL - (now - ts))
                raise ValueError(f"发送太频繁，请 {remaining} 秒后再试")

        code = _gen_code()
        _store[email] = (code, now)

    send_verification_email(email, code)


def verify_code(email: str, code: str) -> bool:
    """Return True if the code matches and hasn't expired. Consumes on success."""
    email = email.strip().lower()
    now = time.time()

    with _lock:
        entry = _store.get(email)
        if not entry:
            return False
        stored_code, ts = entry
        if now - ts > _CODE_TTL:
            del _store[email]
            return False
        if stored_code != code.strip():
            return False
        del _store[email]
        return True
