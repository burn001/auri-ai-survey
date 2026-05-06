"""구 invite 템플릿(사례품 미안내) 수신자 중 미응답자에게 재안내 메일 발송.

대상 (모두 충족):
- participants.email_sent == true (1차 invite 발송 완료)
- participants.email_sent_at < OLD_INVITE_CUTOFF (3fcc436 배포 이전 발송분만)
- participants.bounced != true / email_invalid != true
- participants.source != 'staff' / category != '연구진'
- email 존재
- 응답 미제출 (responses.submitted_at != null 인 토큰에 미해당)
- 이미 reward_resend 보낸 토큰 제외 (email_logs.type='reward_resend', status='sent')

체크포인트: 매 발송마다 email_logs 즉시 기록 → 재실행 시 이미 sent 토큰 자동 dedup.
한도 초과(5.4.5 / daily / limit) 재감지 시 즉시 abort.
페이스: 발송 간 2초 sleep.

운영:
  type backend\\templates\\reward_resend.html | docker exec -i auri-survey-api tee /tmp/reward_resend.html > nul
  type scripts\\send_reward_resend.py        | docker exec -i auri-survey-api tee /tmp/reward_resend.py  > nul
  docker exec auri-survey-api python /tmp/reward_resend.py --dry-run
  docker exec auri-survey-api python /tmp/reward_resend.py            # 실 발송
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


# 3fcc436 backend 컨테이너 배포 시각 (UTC). 이 시각 이전에 email_sent_at 이 찍힌 invite 는
# 사례품 안내가 없던 옛 템플릿으로 발송된 것 → 본 재안내 대상.
OLD_INVITE_CUTOFF = datetime(2026, 5, 6, 3, 40, 56)


def _load_template() -> str:
    for p in (Path('/tmp/reward_resend.html'), Path('/app/templates/reward_resend.html')):
        if p.exists():
            return p.read_text(encoding='utf-8')
    raise FileNotFoundError('reward_resend.html (검색 위치: /tmp, /app/templates)')


def _render(name: str, org: str, survey_url: str, tpl: str) -> str:
    return (tpl.replace('{{name}}', name or '응답자')
               .replace('{{org}}', org or '')
               .replace('{{survey_url}}', survey_url))


async def main(dry_run: bool, limit: int) -> int:
    s = get_settings()
    if not s.GMAIL_USER or not s.GMAIL_APP_PASSWORD:
        print('[abort] GMAIL_USER/GMAIL_APP_PASSWORD 환경변수 없음')
        return 1

    tpl = _load_template()

    await connect()
    db = get_db()

    submitted = set(await db.responses.distinct('token', {'submitted_at': {'$ne': None}}))
    print(f'submitted 응답: {len(submitted)}')

    already = set(await db.email_logs.distinct(
        'token', {'type': 'reward_resend', 'status': 'sent'}
    ))
    print(f'이미 reward_resend sent: {len(already)}')

    cursor = db.participants.find(
        {
            'email_sent': True,
            'email_sent_at': {'$lt': OLD_INVITE_CUTOFF},
            'bounced': {'$ne': True},
            'email_invalid': {'$ne': True},
            'source': {'$ne': 'staff'},
            'category': {'$ne': '연구진'},
            'email': {'$exists': True, '$ne': ''},
        },
        {'_id': 0, 'token': 1, 'email': 1, 'name': 1, 'org': 1, 'category': 1, 'email_sent_at': 1},
    )
    raw = await cursor.to_list(length=20000)
    candidates = [c for c in raw if c['token'] not in submitted and c['token'] not in already]
    print(f'대상자 (옛 invite 수신·미응답·미발송): {len(candidates)}')

    if limit and limit > 0:
        candidates = candidates[:limit]
        print(f'limit 적용: {len(candidates)}')

    base = (s.SURVEY_BASE_URL or '').rstrip('/')
    sent_n = 0
    failed = []
    aborted = False

    for i, p in enumerate(candidates, 1):
        token = p['token']
        email = p['email']
        name = p.get('name', '')
        org = p.get('org', '')
        category = p.get('category', '')
        survey_url = f"{base}/?token={token}"
        subject = '[AURI 건축AI 실무자 조사] 사례품 안내 — 다시 한번 부탁드립니다'
        html = _render(name, org, survey_url, tpl)

        if dry_run:
            print(f'[{i}/{len(candidates)}] [dry] {email} ({name} / {category})')
            continue

        try:
            send_email(email, subject, html)
            await db.email_logs.insert_one({
                'batch_id': 'reward-resend',
                'token': token,
                'email': email,
                'name': name,
                'org': org,
                'category': category,
                'type': 'reward_resend',
                'subject': subject,
                'admin_email': 'system',
                'admin_name': '사례품 재안내',
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
                    'batch_id': 'reward-resend',
                    'token': token,
                    'email': email,
                    'name': name,
                    'org': org,
                    'category': category,
                    'type': 'reward_resend',
                    'subject': subject,
                    'admin_email': 'system',
                    'admin_name': '사례품 재안내',
                    'sent_at': datetime.utcnow(),
                    'status': 'failed',
                    'error': err,
                })
            except Exception:
                pass
            failed.append({'token': token, 'email': email, 'error': err})
            if '5.4.5' in err or 'daily' in err.lower() or 'limit exceeded' in err.lower():
                aborted = True
                remain = len(candidates) - i
                print(f'[ABORT] Gmail 한도 초과 — {sent_n} sent, {remain} 토큰 남음. 한도 풀린 후 재실행 시 이미 sent 토큰은 자동 제외됩니다.')
                break
            time.sleep(3)

    print(f'\n=== summary ===')
    print(f'sent={sent_n} failed={len(failed)} aborted={aborted}')
    if failed:
        for f in failed[:3]:
            print(f"  - {f['email']}: {f['error'][:120]}")

    await disconnect()
    return 2 if aborted else 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.dry_run, args.limit)))
