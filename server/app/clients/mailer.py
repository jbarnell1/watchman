"""Outgoing email behind one interface. SmtpMailer sends; FakeMailer records."""

from __future__ import annotations

import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import make_msgid

from app.settings import Smtp


@dataclass
class Attachment:
    cid: str
    data: bytes
    mime: str = "image/webp"
    filename: str = "image.webp"


@dataclass
class Mail:
    to: list[str]
    subject: str
    text: str
    html: str | None = None
    inline: list[Attachment] = field(default_factory=list)


class Mailer:
    def send(self, mail: Mail) -> None:
        raise NotImplementedError


class SmtpMailer(Mailer):
    def __init__(self, smtp: Smtp) -> None:
        self.smtp = smtp

    def configured(self) -> bool:
        return bool(self.smtp.host and self.smtp.from_address)

    def send(self, mail: Mail) -> None:
        if not self.configured():
            raise RuntimeError("SMTP is not configured")
        msg = EmailMessage()
        msg["From"] = self.smtp.from_address
        msg["To"] = ", ".join(mail.to)
        msg["Subject"] = mail.subject
        msg["Message-ID"] = make_msgid()
        msg.set_content(mail.text)
        if mail.html:
            msg.add_alternative(mail.html, subtype="html")
            html_part = msg.get_payload()[-1]
            for att in mail.inline:
                maintype, subtype = att.mime.split("/", 1)
                html_part.add_related(att.data, maintype=maintype, subtype=subtype,
                                      cid=f"<{att.cid}>", filename=att.filename)
        with smtplib.SMTP(self.smtp.host, self.smtp.port, timeout=30) as s:
            if self.smtp.starttls:
                s.starttls()
            if self.smtp.username:
                s.login(self.smtp.username, self.smtp.password)
            s.send_message(msg)


class FakeMailer(Mailer):
    def __init__(self) -> None:
        self.sent: list[Mail] = []

    def send(self, mail: Mail) -> None:
        self.sent.append(mail)
