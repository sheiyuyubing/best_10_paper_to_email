from __future__ import annotations

import os
import smtplib
import ssl
from datetime import date
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Iterable

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .models import Paper


class EmailConfigError(RuntimeError):
    pass


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise EmailConfigError("缺少环境变量：" + name)
    return value


def recipients_from_env() -> list[str]:
    raw = required_env("MAIL_TO")
    values = raw.replace(';', ',').split(',')
    recipients = [value.strip() for value in values if value.strip()]
    if not recipients:
        raise EmailConfigError("MAIL_TO 中没有有效收件地址")
    return recipients


def render_digest(papers: Iterable[Paper], template_dir: Path) -> tuple[str, str]:
    paper_list = list(papers)
    environment = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    html = environment.get_template("digest.html").render(
        papers=paper_list,
        today=date.today().isoformat(),
    )
    lines = ["计算机顶刊顶会论文精选", "日期：" + date.today().isoformat(), ""]
    for index, paper in enumerate(paper_list, 1):
        published = paper.publication_date.isoformat() if paper.publication_date else "未知日期"
        lines.extend([
            str(index) + ". " + paper.title,
            "作者：" + ("、".join(paper.authors) if paper.authors else "未知"),
            "来源：" + paper.venue + " | " + published,
            "链接：" + (paper.url or "无"),
            "摘要：" + (paper.abstract or "暂无摘要"),
            "",
        ])
    return "\n".join(lines).strip(), html


def smtp_login(server: smtplib.SMTP, username: str, password: str) -> None:
    server.ehlo_or_helo_if_needed()
    advertised = server.esmtp_features.get("auth", "").split()
    if "LOGIN" not in advertised:
        server.login(username, password)
        return
    server.user = username
    server.password = password
    server.auth("LOGIN", server.auth_login, initial_response_ok=False)


def send_digest(papers: Iterable[Paper], template_dir: Path) -> None:
    paper_list = list(papers)
    if not paper_list:
        raise EmailConfigError("没有可发送的论文")
    host = required_env("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "465"))
    username = required_env("SMTP_USER")
    password = required_env("SMTP_PASSWORD")
    sender = os.getenv("MAIL_FROM", "").strip() or username
    recipients = recipients_from_env()
    use_ssl = os.getenv("SMTP_USE_SSL", "true").strip().lower() in {"1", "true", "yes", "on"}
    timeout = int(os.getenv("SMTP_TIMEOUT_SECONDS", "30"))
    plain_text, html = render_digest(paper_list, template_dir)
    message = EmailMessage()
    message["Subject"] = "计算机顶刊顶会论文精选 | " + date.today().isoformat()
    message["From"] = formataddr(("Paper Digest", sender))
    message["To"] = ", ".join(recipients)
    message.set_content(plain_text)
    message.add_alternative(html, subtype="html")
    context = ssl.create_default_context()
    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=timeout, context=context) as server:
            smtp_login(server, username, password)
            server.send_message(message)
        return
    with smtplib.SMTP(host, port, timeout=timeout) as server:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        smtp_login(server, username, password)
        server.send_message(message)
