import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from src.config import settings


class EmailService:
    @staticmethod
    def send_report(subject: str, content: str) -> tuple[bool, str]:
        try:
            # 1. Parse comma-separated list of emails from .env
            recipients = [
                email.strip()
                for email in settings.email_receiver.split(",")
                if email.strip()
            ]

            if not recipients:
                return False, "No valid recipient emails found in EMAIL_RECEIVER."

            # 2. Build the MIME email message
            msg = MIMEMultipart()
            msg["From"] = settings.email_sender
            msg["To"] = ", ".join(
                recipients
            )  # Shows all recipients in the email header
            msg["Subject"] = subject

            # Attach HTML content
            msg.attach(MIMEText(content, "html", "utf-8"))

            # 3. Connect to SMTP and send to all recipients
            with smtplib.SMTP(settings.smtp_server, settings.smtp_port) as server:
                server.starttls()
                server.login(settings.email_sender, settings.email_password)

                # server.sendmail requires a list of recipient emails: ['email1@...", 'email2@...']
                server.sendmail(settings.email_sender, recipients, msg.as_string())

            return True, f"Email sent successfully to {len(recipients)} recipient(s)!"

        except Exception as e:
            return False, f"SMTP Error details: {str(e)}"
