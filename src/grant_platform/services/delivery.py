"""How a rendered message leaves the platform.

Two channels share one shape, deliver(message, file_name):
- the local outbox: a folder of text files. Nothing is emailed. This is the default and always works;
- SMTP: really sends the message. It is OFF unless every one of these holds: EMAIL_DELIVERY=smtp, the SMTP host, sender
  address and an allow-list of recipients are all configured, and the recipient is on the allow-list. The allow-list is
  required in this phase so that a mistake can never email a stranger. Every sent message is also kept in the outbox.

Credentials come from the environment or the git-ignored .env file and are never printed, logged or stored.
File names are built from numbers and dates only, never from user text.
"""
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

ENV_NAMES = ("EMAIL_DELIVERY", "EMAIL_FROM", "EMAIL_ALLOWLIST", "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_STARTTLS")
OUTBOX_NOTE = "This message was written to the local outbox. It was not emailed."
SENT_NOTE = "A copy of this message was kept here before it was emailed. The message log shows whether it was sent."
DEFAULT_PORT = 587
TIMEOUT_SECONDS = 20


class DeliveryError(Exception):
    """A message could not be delivered. The text never contains credentials."""


@dataclass(frozen=True)
class Message:
    to_name: str
    to_email: str
    subject: str
    body: str

    def address(self):
        return f"{self.to_name} <{self.to_email}>"


@dataclass(frozen=True)
class EmailSettings:
    mode: str = "outbox"
    host: str = ""
    port: int = DEFAULT_PORT
    username: str = ""
    password: str = ""
    sender: str = ""
    allowlist: tuple = ()
    starttls: bool = True

    @classmethod
    def from_env(cls, environ=None):
        environ = os.environ if environ is None else environ
        text = lambda name: str(environ.get(name) or "").strip()
        try:
            port = int(text("SMTP_PORT") or DEFAULT_PORT)
        except ValueError:
            port = -1
        return cls(
            mode=text("EMAIL_DELIVERY").lower() or "outbox", host=text("SMTP_HOST"), port=port, username=text("SMTP_USERNAME"),
            password=str(environ.get("SMTP_PASSWORD") or ""), sender=text("EMAIL_FROM"),
            allowlist=tuple(entry.strip().lower() for entry in text("EMAIL_ALLOWLIST").split(",") if entry.strip()),
            starttls=text("SMTP_STARTTLS").lower() not in ("0", "false", "no", "off"),
        )

    def problems(self):
        """Why real sending cannot happen yet (empty when everything needed is set)."""
        found = []
        if self.mode != "smtp":
            found.append("EMAIL_DELIVERY is not set to smtp")
        if not self.host:
            found.append("SMTP_HOST is not set")
        if not 1 <= self.port <= 65535:
            found.append("SMTP_PORT is not a valid port number")
        if not self.sender or "@" not in self.sender or any(ch in self.sender for ch in "\r\n<> "):
            found.append("EMAIL_FROM is not set to a plain email address")
        if bool(self.username) != bool(self.password):
            found.append("SMTP_USERNAME and SMTP_PASSWORD must be set together")
        if not self.allowlist:
            found.append("EMAIL_ALLOWLIST is empty (only listed addresses may be emailed in this phase)")
        return found

    @property
    def enabled(self):
        return not self.problems()

    def allows(self, address):
        address = str(address).strip().lower()
        domain = address.rpartition("@")[2]
        return any(entry == address or entry in (domain, "@" + domain) for entry in self.allowlist)

    def describe(self):
        """One sentence for people. Mentions which settings are missing, never their values."""
        if self.enabled:
            return f"Email sending is ON for {len(self.allowlist)} allow-listed recipient{'s' if len(self.allowlist) != 1 else ''}"
        if self.mode != "smtp":
            return "Email sending is OFF: messages are written to the local outbox and nothing is emailed"
        return "Email sending is OFF (not fully set up): " + "; ".join(self.problems())


def outbox_channel(outbox, note=OUTBOX_NOTE):
    """A channel that writes each message to outbox/<name> (atomically). Returns the file name written."""
    outbox = Path(outbox)

    def deliver(message, name):
        outbox.mkdir(parents=True, exist_ok=True)
        text = f"To: {message.address()}\nSubject: {message.subject}\n\n{message.body.rstrip()}\n\n{note}\n"
        temporary = outbox / f".{name}.tmp"
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, outbox / name)
        return name

    return deliver


def build_email(settings, message):
    """The wire message. EmailMessage refuses line breaks in headers, so text cannot add a header."""
    email = EmailMessage()
    sender_domain = settings.sender.rpartition("@")[2]
    try:
        email["From"] = Address("Grant Management Platform", addr_spec=settings.sender)
        email["To"] = Address(message.to_name, addr_spec=message.to_email)
        email["Subject"] = message.subject
    except (ValueError, IndexError) as error:
        raise DeliveryError(f"The message could not be built ({type(error).__name__})") from None
    email["Date"] = formatdate(localtime=False)
    email["Message-ID"] = make_msgid(domain=sender_domain)
    email["Auto-Submitted"] = "auto-generated"
    email.set_content(message.body.rstrip() + "\n", charset="utf-8")
    return email


def send_smtp(settings, message, smtp_factory=smtplib.SMTP):
    """Send one message. Raises DeliveryError (with no credentials in the text) on any failure."""
    email = build_email(settings, message)
    try:
        with smtp_factory(settings.host, settings.port, timeout=TIMEOUT_SECONDS) as connection:
            if settings.starttls:
                connection.starttls(context=ssl.create_default_context())
            if settings.username:
                connection.login(settings.username, settings.password)
            connection.send_message(email, from_addr=settings.sender, to_addrs=[message.to_email])
    except smtplib.SMTPResponseException as error:
        raise DeliveryError(f"The mail server refused the message (code {error.smtp_code})") from None
    except (smtplib.SMTPException, OSError, ssl.SSLError) as error:
        raise DeliveryError(f"The mail server could not be reached or refused the message ({type(error).__name__})") from None


def channel_for(settings, outbox, smtp_factory=smtplib.SMTP):
    """The outbox unless sending is fully set up. When it is, each message is first kept in the outbox, then emailed,
    and only to allow-listed recipients."""
    if not settings.enabled:
        return outbox_channel(outbox)
    keep = outbox_channel(outbox, SENT_NOTE)

    def deliver(message, name):
        if not settings.allows(message.to_email):
            raise DeliveryError("This address is not on the recipient allow-list, so it was not emailed")
        keep(message, name)
        send_smtp(settings, message, smtp_factory)
        return name

    return deliver
