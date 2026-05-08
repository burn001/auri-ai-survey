"""사례품 발송 작업 시작 시 Telegram 알림.

run_reward_dispatch.bat의 Phase 0에서 호출. DB 쿼리 없이 환경변수만 사용.

환경변수:
- TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  (없으면 조용히 종료)
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))


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


def main() -> int:
    bot = (os.environ.get('TELEGRAM_BOT_TOKEN') or '').strip()
    chat = (os.environ.get('TELEGRAM_CHAT_ID') or '').strip()
    if not bot or not chat:
        print('[TELEGRAM] env 없음 — skip')
        return 0
    now_kst = datetime.now(KST).strftime('%Y-%m-%d %H:%M KST')
    text = (
        '🚀 [AURI 설문] 사례품 발송 작업 시작\n'
        f'시각: {now_kst}\n'
        '\n'
        '— Phase 1 응답자 미동의 안내 (reward_notice)\n'
        '— Phase 2 미응답자 재안내 (reward_resend)\n'
        '\n'
        '발송 종료 후 결과 리포트가 별도로 전달됩니다.'
    )
    print(text)
    print('---')
    _send_telegram(bot, chat, text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
