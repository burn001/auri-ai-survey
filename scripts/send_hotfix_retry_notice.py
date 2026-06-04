"""5/20 hotfix 영향 응답자에게 사과 + 재시도 안내 메일 1건 발송.

배경:
  - 2026-05-20 09:46 KST commit 908027c 배포 시 frontend `CATEGORY_TO_Q6_INDEX` 정의 누락.
  - 응답자가 인트로 [설문 시작하기] 버튼을 누르면 /start 200 응답 후 ReferenceError →
    "네트워크 오류가 발생했습니다" alert + 본문 진입 실패 (started_at 박힘, submitted_at 없음).
  - 2026-05-20 16:25 KST commit 27a496a hotfix 배포 완료. 동일 토큰 재진입 시 정상 동작.

대상 토큰·이메일은 인자로 받는다(get_target_token.py 로 조회). email_logs.type
'hotfix_retry_notice' 로 dedup — 동일 토큰 재발송 차단. db_safety: 기본 dry-run.

사용:
  docker exec auri-survey-api python /app/scripts/send_hotfix_retry_notice.py \
      --token <token> --email <email> [--apply]
"""
import argparse
import asyncio
from datetime import datetime

import sys
sys.path.insert(0, "/app")
from services.db import connect, get_db
from services.email_service import send_email
from config import get_settings


def build_html(name: str, org: str, survey_url: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:'Noto Sans KR',-apple-system,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f5f5;padding:40px 0">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.06)">

  <tr><td style="background:#2c2c2c;padding:28px 32px">
    <p style="margin:0;color:#ffffff;font-size:13px;font-weight:500;letter-spacing:0.05em">건축공간연구원 (AURI)</p>
    <h1 style="margin:8px 0 0;color:#ffffff;font-size:18px;font-weight:700;line-height:1.5">건축 분야 AI 실무자 설문조사<br>응답 진행 오류 안내 및 재참여 요청</h1>
  </td></tr>

  <tr><td style="padding:32px">
    <p style="font-size:15px;color:#1a1a1a;line-height:1.8;margin:0 0 20px">
      <strong>{name}</strong> 님 ({org}) 안녕하십니까.
    </p>
    <p style="font-size:14px;color:#444;line-height:1.8;margin:0 0 16px">
      건축공간연구원에서 수행 중인 <strong>건축 분야 AI 기술 도입에 따른 변화 분석 연구</strong>에 관심을 가지시고 설문 페이지에 접속해 주셔서 진심으로 감사드립니다.
    </p>
    <div style="background:#fef3c7;border-left:4px solid #d97706;padding:14px 16px;margin:0 0 18px;border-radius:4px">
      <p style="margin:0 0 8px;font-weight:600;color:#92400e;font-size:14px">시스템 일시 오류 안내</p>
      <p style="margin:0;font-size:13px;color:#78350f;line-height:1.7">
        2026년 5월 20일 오전 시스템 업데이트 과정에서 일시적인 화면 진입 오류가 있어, 인트로 페이지에서 <strong>“설문 시작하기”</strong> 버튼을 누르신 후 본문 응답 화면이 정상적으로 표시되지 않은 점, 정중히 사과드립니다.
      </p>
    </div>
    <p style="font-size:14px;color:#444;line-height:1.8;margin:0 0 16px">
      해당 오류는 동일일 16:25 KST에 모두 수정 배포되었으며, 아래 링크로 다시 접속해 주시면 응답 본문이 정상적으로 표시됩니다. 이전에 입력하신 정보는 그대로 유지됩니다.
    </p>
    <p style="font-size:14px;color:#444;line-height:1.8;margin:0 0 24px">
      귀하의 응답이 결과의 신뢰도에 매우 중요합니다. 짧은 시간이라도 응답 참여 부탁드립니다.
    </p>

    <table cellpadding="0" cellspacing="0" style="margin:0 auto 24px">
    <tr><td align="center" style="background:#4f46e5;border-radius:6px">
      <a href="{survey_url}" style="display:inline-block;padding:14px 36px;color:#ffffff;font-size:15px;font-weight:600;text-decoration:none">설문 응답 이어 진행하기 →</a>
    </td></tr>
    </table>

    <p style="font-size:12px;color:#9ca3af;line-height:1.7;margin:24px 0 0;border-top:1px solid #e5e7eb;padding-top:16px">
      ※ 본 메일은 5월 20일 오전 시스템 오류 시점에 접속하신 응답자께만 1회 발송되었습니다.<br>
      ※ 응답 완료 시 사례품(2만원 상당 모바일 상품권) 안내가 표시됩니다. 사례품 수령을 원하시는 경우 인트로의 사례품 동의 카드에서 휴대전화 번호를 입력해 주십시오.
    </p>
  </td></tr>

  <tr><td style="background:#f9fafb;padding:20px 32px;border-top:1px solid #e5e7eb">
    <p style="margin:0;font-size:11px;color:#6b7280;line-height:1.7">
      <strong>건축공간연구원 (AURI)</strong> · 연구책임: 남성우 부연구위원 (swnam@auri.re.kr / 044-417-9693)<br>
      본 메일은 시스템 오류 보완 안내 목적으로 발송되었으며, 마케팅 메일이 아닙니다.
    </p>
  </td></tr>
</table>
</td></tr>
</table>
</body></html>"""


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True)
    ap.add_argument("--email", required=True, help="대상 이메일(토큰 doc과 일치 확인용)")
    ap.add_argument("--apply", action="store_true", help="실제 발송. 미지정 시 dry-run")
    args = ap.parse_args()

    await connect()
    d = get_db()
    s = get_settings()

    participant = await d.participants.find_one({"token": args.token})
    if not participant:
        print(f"FAIL: token {args.token} not found")
        return
    if participant.get("email") != args.email:
        print(f"FAIL: email mismatch — expected {args.email}, got {participant.get('email')}")
        return

    dup = await d.email_logs.count_documents({
        "token": args.token,
        "type": "hotfix_retry_notice",
        "status": "sent",
    })
    if dup:
        print(f"SKIP: hotfix_retry_notice already sent for {args.token} ({dup} records)")
        return

    name = participant.get("name", "응답자")
    org = participant.get("org", "")
    survey_url = f"{s.SURVEY_BASE_URL}/?token={args.token}"
    subject = "[AURI 건축AI 실무자 조사] 5/20 응답 진행 오류 안내 및 재참여 요청"
    html = build_html(name, org, survey_url)

    print(f"=== Target ===")
    print(f"  name:    {name}")
    print(f"  email:   {args.email}")
    print(f"  org:     {org}")
    print(f"  cat:     {participant.get('category', '')}")
    print(f"  token:   {args.token}")
    print(f"  url:     {survey_url}")
    print(f"  subject: {subject}")
    print(f"  apply:   {args.apply}")

    if not args.apply:
        print("\n=== DRY RUN: 메일 미발송 (--apply 필요) ===")
        return

    now = datetime.utcnow()
    log_doc = {
        "batch_id": "hotfix-retry-notice-2026-05-20",
        "token": args.token,
        "email": args.email,
        "name": name,
        "org": org,
        "category": participant.get("category", ""),
        "type": "hotfix_retry_notice",
        "subject": subject,
        "admin_email": "system",
        "admin_name": "hotfix 보완 안내",
        "sent_at": now,
    }
    try:
        send_email(args.email, subject, html)
        log_doc.update({"status": "sent", "error": ""})
        await d.email_logs.insert_one(log_doc)
        print(f"\nSENT: {args.email} at {now.isoformat()}Z (UTC)")
    except Exception as e:
        log_doc.update({"status": "failed", "error": str(e)[:500]})
        await d.email_logs.insert_one(log_doc)
        print(f"\nFAILED: {e}")


if __name__ == "__main__":
    asyncio.run(main())
