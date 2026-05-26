"""invite 일괄 발송 종료 후 Telegram 결과 보고.

run_survey_dispatch.bat의 Phase 5에서 호출. today(KST) 기준 invite sent/failed 집계 + wave4.json 토큰 중 미발송 잔여 카운트.
wave1~3은 발송 완료되어 보고 대상에서 제외(2026-05-26).
"""
import asyncio
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, '/app')

from services.db import connect, disconnect, get_db


KST = timezone(timedelta(hours=9))
WAVES = [4]
SCRIPTS_DIR = Path('/app/scripts')


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

    invite_sent_today = await db.email_logs.count_documents({
        'type': 'invite', 'status': 'sent', 'sent_at': {'$gte': start_utc_naive},
    })
    invite_fail_today = await db.email_logs.count_documents({
        'type': 'invite', 'status': 'failed', 'sent_at': {'$gte': start_utc_naive},
    })

    all_tokens = []
    for wn in WAVES:
        wp = SCRIPTS_DIR / f'wave{wn}.json'
        if not wp.exists():
            continue
        data = json.loads(wp.read_text(encoding='utf-8'))
        for sub in data.get('tokens', []):
            all_tokens.extend(sub)

    sent_tokens = set(await db.participants.distinct(
        'token', {'token': {'$in': all_tokens}, 'email_sent': True}
    ))
    bounced = set(await db.participants.distinct(
        'token', {'token': {'$in': all_tokens},
                  '$or': [{'bounced': True}, {'email_invalid': True}]}
    ))
    remain = sum(1 for t in all_tokens if t not in sent_tokens and t not in bounced)

    now_kst = datetime.now(KST).strftime('%Y-%m-%d %H:%M KST')
    sent_cnt = len(sent_tokens)

    lines = [
        '📬 [AURI 설문] wave4 invite 발송 결과',
        f'시각: {now_kst}',
        '',
        '— Phase 4 wave4 invite (사례품 안내 포함)',
        f'   오늘 sent {invite_sent_today} / failed {invite_fail_today}',
        f'   wave4 큐: 전체 {len(all_tokens)} / 발송완료 {sent_cnt} / 잔여 {remain}',
        '',
    ]
    if remain > 0:
        lines.append(f'⚠️ wave4 잔여 {remain}건 — Gmail 한도 또는 abort로 미발송.')
        lines.append('   다음 실행(익일 정오) 시 dedup하여 자동 이어집니다.')
    elif invite_fail_today > 0:
        lines.append('✅ wave4 잔여 0 — 단, 오늘 일부 실패 건 있었음(상단 failed 확인).')
    else:
        lines.append('✅ wave4 전 토큰 발송 완료.')

    text = '\n'.join(lines)
    print(text)
    print('---')
    _send_telegram(bot, chat, text)

    await disconnect()
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
