import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from config import get_settings


def _load_template(filename: str = "survey_invite.html") -> str:
    tpl_path = Path(__file__).parent.parent / "templates" / filename
    return tpl_path.read_text(encoding="utf-8")


def render_email(name: str, org: str, survey_url: str) -> str:
    html = _load_template("survey_invite.html")
    return html.replace("{{name}}", name).replace("{{org}}", org).replace("{{survey_url}}", survey_url)


def render_completion(name: str, org: str, review_url: str) -> str:
    """응답 제출 직후 자동 발송 — 본인용 확인 링크 포함."""
    html = _load_template("survey_complete.html")
    return (html.replace("{{name}}", name)
                .replace("{{org}}", org)
                .replace("{{review_url}}", review_url))


def render_custom(name: str, org: str, survey_url: str, body_html: str) -> str:
    """관리자 자유 본문에 동일 placeholder 치환 적용. 줄바꿈만 입력한 plain text는 <br>로 변환."""
    html = body_html or ""
    if "<p" not in html and "<br" not in html and "<div" not in html:
        html = html.replace("\r\n", "\n").replace("\n", "<br>")
    html = (html.replace("{{name}}", name)
                .replace("{{org}}", org)
                .replace("{{survey_url}}", survey_url))
    return (
        '<div style="font-family:\'Noto Sans KR\',sans-serif;'
        'font-size:14px;line-height:1.7;color:#222;max-width:640px;margin:0 auto;'
        'padding:24px">' + html + '</div>'
    )


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


def send_email_multi(to_list, cc_list=None, *, subject: str, html_body: str) -> bool:
    """다중 To + Cc 발송. Cc 빈 리스트도 허용."""
    s = get_settings()
    cc_list = cc_list or []
    msg = MIMEMultipart("alternative")
    msg["From"] = f"AURI 건축AI연구 <{s.GMAIL_USER}>"
    msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    server = smtplib.SMTP("smtp.gmail.com", 587)
    server.starttls()
    server.login(s.GMAIL_USER, s.GMAIL_APP_PASSWORD)
    server.sendmail(s.GMAIL_USER, to_list + cc_list, msg.as_string())
    server.quit()
    return True
