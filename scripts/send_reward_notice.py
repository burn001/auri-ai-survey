"""기존 응답자 중 사례품 미동의자에게 안내 메일 발송.

대상 (모두 충족):
- responses.submitted_at != null
- participants.consent_reward != true
- participants.source != 'staff'
- participants.category != '연구진'
- 이미 reward_notice 메일 받지 않은 토큰 (email_logs.type='reward_notice', status='sent' 없음)

운영:
  docker exec -i auri-survey-api tee /tmp/reward_notice.py < scripts/send_reward_notice.py
  docker exec auri-survey-api python /tmp/reward_notice.py --dry-run
  docker exec auri-survey-api python /tmp/reward_notice.py            # 실 발송

페이스: 발송 간 2초 sleep, 한도 초과 (5.4.5 / daily / limit) 재감지 시 즉시 abort.
"""
import argparse
import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, '/app')

from services.db import connect, disconnect, get_db
from services.email_service import send_email
from config import get_settings


def _load_template() -> str:
    p = Path('/app/templates/reward_notice.html')
    return p.read_text(encoding='utf-8')


def _render(name: str, org: str, review_url: str, tpl: str) -> str:
    return (tpl.replace('{{name}}', name or '응답자')
               .replace('{{org}}', org or '')
               .replace('{{review_url}}', review_url))


async def main(dry_run: bool, limit: int) -> int:
    s = get_settings()
    if not s.GMAIL_USER or not s.GMAIL_APP_PASSWORD:
        print('[abort] GMAIL_USER/GMAIL_APP_PASSWORD 환경변수 없음')
        return 1

    tpl = _load_template()

    await connect()
    db = get_db()

    # 1. 응답 제출자 토큰 풀
    submitted = set(await db.responses.distinct('token', {'submitted_at': {'$ne': None}}))
    print(f'submitted 응답: {len(submitted)}')

    # 2. 이미 reward_notice 받은 토큰 (재발송 방지)
    already = set(await db.email_logs.distinct(
        'token', {'type': 'reward_notice', 'status': 'sent'}
    ))
    print(f'이미 reward_notice sent: {len(already)}')

    # 3. participants 필터 — 동의 X, staff/연구진 X
    cursor = db.participants.find(
        {
            'token': {'$in': list(submitted)},
            'source': {'$ne': 'staff'},
            'category': {'$ne': '연구진'},
            '$or': [
                {'consent_reward': {'$ne': True}},
                {'consent_reward': {'$exists': False}},
            ],
            'email': {'$exists': True, '$ne': ''},
        },
        {'_id': 0, 'token': 1, 'email': 1, 'name': 1, 'org': 1, 'category': 1, 'consent_reward': 1},
    )
    candidates = await cursor.to_list(length=20000)
    candidates = [c for c in candidates if c['token'] not in already]
    print(f'대상자 (동의 X, staff·연구진 제외, 미발송): {len(candidates)}')

    if limit and limit > 0:
        candidates = candidates[:limit]
        print(f'limit 적용: {len(candidates)}')

    base = (s.SURVEY_BASE_URL or '').rstrip('/')
    sent_n = 0
    failed = []

    for i, p in enumerate(candidates, 1):
        token = p['token']
        email = p['email']
        name = p.get('name', '')
        org = p.get('org', '')
        category = p.get('category', '')
        review_url = f"{base}/?token={token}&review=1"
        subject = '[AURI 건축AI 실무자 조사] 사례품 발송 안내 — 동의 절차 안내'
        html = _render(name, org, review_url, tpl)

        if dry_run:
            print(f'[{i}/{len(candidates)}] [dry] {email} ({name} / {category})')
            continue

        try:
            send_email(email, subject, html)
            await db.email_logs.insert_one({
                'batch_id': 'reward-notice',
                'token': token,
                'email': email,
                'name': name,
                'org': org,
                'category': category,
                'type': 'reward_notice',
                'subject': subject,
                'admin_email': 'system',
                'admin_name': '사례품 안내',
                'sent_at': datetime.utcnow(),
                'status': 'sent',
                'error': '',
            })
            print(f'[{i}/{len(candidates)}] sent {email} ({name})')
            sent_n += 1
            time.sleep(2)
        except Exception as e:
            err = str(e)[:300]
            print(f'[{i}/{len(candidates)}] FAIL {email}: {err}')
            try:
                await db.email_logs.insert_one({
                    'batch_id': 'reward-notice',
                    'token': token,
                    'email': email,
                    'name': name,
                    'org': org,
                    'category': category,
                    'type': 'reward_notice',
                    'subject': subject,
                    'admin_email': 'system',
                    'admin_name': '사례품 안내',
                    'sent_at': datetime.utcnow(),
                    'status': 'failed',
                    'error': err,
                })
            except Exception:
                pass
            failed.append({'token': token, 'email': email, 'error': err})
            if '5.4.5' in err or 'daily' in err.lower() or 'limit exceeded' in err.lower():
                print('[ABORT] Gmail 한도 초과 — 한도 풀린 후 다시 실행하십시오.')
                break
            time.sleep(3)

    print(f'\n=== summary ===')
    print(f'sent={sent_n} failed={len(failed)}')
    if failed:
        for f in failed[:3]:
            print(f"  - {f['email']}: {f['error'][:120]}")

    await disconnect()
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.dry_run, args.limit)))
