import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def build_message(data: dict, from_addr: str) -> MIMEText:
    msg = MIMEText(data["email_body"], "plain")
    msg["From"] = from_addr
    msg["To"] = data["email"]
    msg["Subject"] = data["email_subject"]
    return msg


def send(data: dict) -> bool:
    if not data.get("email"):
        return False

    msg = build_message(data, from_addr=GMAIL_ADDRESS)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.send_message(msg)

    return True
