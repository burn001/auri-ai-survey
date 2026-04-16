import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from config import get_settings


def _load_template() -> str:
    tpl_path = Path(__file__).parent.parent / "templates" / "survey_invite.html"
    return tpl_path.read_text(encoding="utf-8")


def render_email(name: str, org: str, survey_url: str) -> str:
    html = _load_template()
    return html.replace("{{name}}", name).replace("{{org}}", org).replace("{{survey_url}}", survey_url)


def send_email(to_email: str, subject: str, html_body: str) -> bool:
    s = get_settings()
    msg = MIMEMultipart("alternative")
    msg["From"] = f"AURI 건축AI연구 <{s.GMAIL_USER}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    server = smtplib.SMTP("smtp.gmail.com", 587)
    server.starttls()
    server.login(s.GMAIL_USER, s.GMAIL_APP_PASSWORD)
    server.sendmail(s.GMAIL_USER, to_email, msg.as_string())
    server.quit()
    return True
