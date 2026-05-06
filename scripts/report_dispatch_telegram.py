"""사례품 안내 일괄 발송 종료 후 Telegram 결과 보고.

run_reward_dispatch.bat의 Phase 3에서 호출.
DB email_logs를 직접 집계해 today(KST) 기준 reward_notice·reward_resend
sent/failed/남음 수를 산출하고 Telegram Bot API로 한 줄 리포트를 보낸다.

환경변수:
- TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  (없으면 조용히 종료)

운영:
  type scripts\\report_dispatch_telegram.py | docker exec -i auri-survey-api tee /tmp/report_dispatch.py > nul
  docker exec -e TELEGRAM_BOT_TOKEN=... -e TELEGRAM_CHAT_ID=... auri-survey-api python /tmp/report_dispatch.py
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
OLD_INVITE_CUTOFF = datetime(2026, 5, 6, 3, 40, 56)


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

    async def cnt(type_: str, status: str) -> int:
        return await db.email_logs.count_documents({
            'type': type_, 'status': status,
            'sent_at': {'$gte': start_utc_naive},
        })

    n_sent = await cnt('reward_notice', 'sent')
    n_fail = await cnt('reward_notice', 'failed')
    r_sent = await cnt('reward_resend', 'sent')
    r_fail = await cnt('reward_resend', 'failed')

    submitted = set(await db.responses.distinct('token', {'submitted_at': {'$ne': None}}))
    notice_done = set(await db.email_logs.distinct(
        'token', {'type': 'reward_notice', 'status': 'sent'}
    ))
    resend_done = set(await db.email_logs.distinct(
        'token', {'type': 'reward_resend', 'status': 'sent'}
    ))

    notice_pool = await db.participants.count_documents({
        'token': {'$in': list(submitted)},
        'source': {'$ne': 'staff'}, 'category': {'$ne': '연구진'},
        '$or': [
            {'consent_reward': {'$ne': True}},
            {'consent_reward': {'$exists': False}},
        ],
        'email': {'$exists': True, '$ne': ''},
    })
    notice_remain = max(0, notice_pool - len(notice_done))

    resend_cursor = db.participants.find(
        {
            'email_sent': True,
            'email_sent_at': {'$lt': OLD_INVITE_CUTOFF},
            'bounced': {'$ne': True},
            'email_invalid': {'$ne': True},
            'source': {'$ne': 'staff'},
            'category': {'$ne': '연구진'},
            'email': {'$exists': True, '$ne': ''},
        },
        {'token': 1, '_id': 0},
    )
    resend_pool_docs = await resend_cursor.to_list(length=20000)
    resend_remain = sum(
        1 for p in resend_pool_docs
        if p['token'] not in submitted and p['token'] not in resend_done
    )

    now_kst = datetime.now(KST).strftime('%Y-%m-%d %H:%M KST')
    aborted = (notice_remain > 0 and notice_pool > 0) or (resend_remain > 0)

    lines = [
        '📨 [AURI 설문] 사례품 안내 발송 결과',
        f'시각: {now_kst}',
        '',
        '— Phase 1 응답자 미동의 안내 (reward_notice)',
        f'   sent {n_sent} / failed {n_fail} / 남음 {notice_remain}',
        '',
        '— Phase 2 미응답자 재안내 (reward_resend)',
        f'   sent {r_sent} / failed {r_fail} / 남음 {resend_remain}',
        '',
    ]
    if aborted:
        lines.append('⚠️ Gmail 한도로 일부 abort된 것으로 보입니다.')
        lines.append('   한도 리셋 후 run_reward_dispatch.bat 재실행 시 자동 이어집니다.')
    else:
        lines.append('✅ 전 대상자 발송 완료.')

    text = '\n'.join(lines)
    print(text)
    print('---')
    _send_telegram(bot, chat, text)

    await disconnect()
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
