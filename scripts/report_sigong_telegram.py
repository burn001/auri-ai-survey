"""시공 reminder 발송 결과 Telegram 보고.

sigong_then_wave4.ps1 Phase A 종료 후 호출. today(KST) 기준 sigong_reminder
sent/failed + 누적 sent + 미발송 잔여 + 시공 Q6 응답/정원 현황을 보고한다.

환경변수: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (없으면 조용히 종료)
"""
import asyncio
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

sys.path.insert(0, '/app')

from services.db import connect, disconnect, get_db


KST = timezone(timedelta(hours=9))
TYPE = 'sigong_reminder'
SIGONG_QUOTA = 75  # responses.py QUOTA_PER_CATEGORY['시공']


def _send_telegram(bot: str, chat: str, text: str) -> bool:
    url = f'https://api.telegram.org/bot{bot}/sendMessage'
    payload = json.dumps({'chat_id': chat, 'text': text, 'disable_web_page_preview': True}).encode('utf-8')
    req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f'[TELEGRAM] HTTP {resp.status}')
            return resp.status == 200
    except Exception as e:
        print(f'[TELEGRAM] FAIL: {e}')
        return False


async def main() -> int:
    bot = (os.environ.get('TELEGRAM_BOT_TOKEN') or '').strip()
    chat = (os.environ.get('TELEGRAM_CHAT_ID') or '').strip()
    if not bot or not chat:
        print('[TELEGRAM] env 없음 — skip')
        return 0

    await connect()
    db = get_db()

    today_kst = datetime.now(KST).date()
    start_kst = datetime.combine(today_kst, datetime.min.time(), tzinfo=KST)
    start_utc_naive = start_kst.astimezone(timezone.utc).replace(tzinfo=None)

    sent_today = await db.email_logs.count_documents(
        {'type': TYPE, 'status': 'sent', 'sent_at': {'$gte': start_utc_naive}})
    fail_today = await db.email_logs.count_documents(
        {'type': TYPE, 'status': 'failed', 'sent_at': {'$gte': start_utc_naive}})
    sent_total = await db.email_logs.count_documents({'type': TYPE, 'status': 'sent'})

    # 미발송 잔여 (send_sigong_reminder.py와 동일 조건)
    submitted = set(await db.responses.distinct('token', {'submitted_at': {'$ne': None}}))
    already = set(await db.email_logs.distinct('token', {'type': TYPE, 'status': 'sent'}))
    cur = db.participants.find(
        {'category': '시공', 'email_sent': True, 'bounced': {'$ne': True},
         'email_invalid': {'$ne': True}, 'source': {'$ne': 'staff'},
         'email': {'$exists': True, '$ne': ''}},
        {'_id': 0, 'token': 1})
    cand = await cur.to_list(length=20000)
    remain = sum(1 for c in cand if c['token'] not in submitted and c['token'] not in already)

    # 시공 정원 현황 — Q6==1(시공) 자기응답 + 사례품 동의
    sigong_consent = await db.responses.count_documents({
        'submitted_at': {'$ne': None}, 'responses.Q6': 1})
    sigong_consent_reward = await db.participants.count_documents({
        'token': {'$in': list(await db.responses.distinct(
            'token', {'submitted_at': {'$ne': None}, 'responses.Q6': 1}))},
        'consent_reward': True, 'source': {'$ne': 'staff'}})

    now_kst = datetime.now(KST).strftime('%Y-%m-%d %H:%M KST')
    lines = [
        '📣 [AURI 설문] 시공 reminder 발송 결과',
        f'시각: {now_kst}',
        '',
        '— 시공 분류 미응답자 재독려',
        f'   오늘 sent {sent_today} / failed {fail_today}',
        f'   누적 sent {sent_total} / 잔여 {remain}',
        '',
        f'— 시공 Q6 응답 {sigong_consent}건 (사례품 동의 {sigong_consent_reward}/{SIGONG_QUOTA})',
    ]
    if remain > 0:
        lines.append(f'⚠️ 시공 미발송 {remain}건 — quota 풀리는 대로 자동 이어집니다.')
    else:
        lines.append('✅ 시공 reminder 전 대상 발송 완료.')

    text = '\n'.join(lines)
    print(text)
    print('---')
    _send_telegram(bot, chat, text)

    await disconnect()
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
