import smtplib
import re
from email.message import EmailMessage
from src.config import settings


class EmailService:
    @staticmethod
    def validate_config() -> list[str]:
        recipients = [
            address.strip()
            for address in settings.email_receiver.split(",")
            if address.strip()
        ]
        email_pattern = r"[^@\s]+@[^@\s]+\.[^@\s]+"
        if not recipients or any(
            re.fullmatch(email_pattern, address) is None for address in recipients
        ):
            raise ValueError("EMAIL_RECEIVER contains an invalid email address")
        if re.fullmatch(email_pattern, settings.email_sender) is None:
            raise ValueError("EMAIL_SENDER is not a valid email address")
        if not settings.smtp_server.strip() or not settings.email_password:
            raise ValueError("SMTP server and email password are required")
        return recipients

    @staticmethod
    def send_report(subject: str, content: str) -> tuple[bool, str]:
        server: smtplib.SMTP | None = None
        try:
            recipients = EmailService.validate_config()
            if "\n" in subject or "\r" in subject:
                raise ValueError("Email subject contains a newline")

            msg = EmailMessage()
            msg["From"] = settings.email_sender
            msg["To"] = ", ".join(recipients)
            msg["Subject"] = subject
            msg.set_content("This digest requires an HTML-capable email client.")
            msg.add_alternative(content, subtype="html")
            server = smtplib.SMTP(
                settings.smtp_server, settings.smtp_port, timeout=settings.smtp_timeout
            )
            server.starttls()
            server.login(settings.email_sender, settings.email_password)
        except Exception as e:
            if server is not None:
                server.close()
            return False, f"NOT_SENT: SMTP setup failed: {e}"

        assert server is not None
        try:
            server.send_message(msg, from_addr=settings.email_sender, to_addrs=recipients)
        except (
            smtplib.SMTPRecipientsRefused,
            smtplib.SMTPSenderRefused,
            smtplib.SMTPDataError,
        ) as e:
            return False, f"NOT_SENT: SMTP rejected message: {e}"
        except Exception as e:
            return False, f"UNKNOWN: SMTP submission outcome: {e}"
        finally:
            try:
                server.quit()
            except Exception:
                pass

        return True, f"Email sent successfully to {len(recipients)} recipient(s)!"
