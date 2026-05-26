"""시공 분야 분류 수신자 중 미응답자에게 1회성 reminder 발송.

배경: 시공 분야 Q6 응답이 타 직군 대비 현저히 저조 → 시공 분류 미응답자에게
재독려 메일을 보낸다. 수신자가 Q6에서 시공이 아닌 다른 직군(설계 제외)을 골라도
설문은 정상 accept되므로(정원 게이트는 '설계+사례품동의+마감'만 차단), 분류 기준
타깃팅으로 충분하다.

대상 (모두 충족):
- participants.category == '시공'
- participants.email_sent == true (이미 invite 수신)
- participants.bounced != true / email_invalid != true
- participants.source != 'staff'
- email 존재
- 응답 미제출 (responses.submitted_at != null 인 토큰에 미해당)
- 이미 sigong_reminder 보낸 토큰 제외 (email_logs.type='sigong_reminder', status='sent')

특징:
- send_reward_resend.py 패턴 복제. 백엔드 services.email_service.send_email 직접 호출.
- participants.email_sent 등 invite 상태는 건드리지 않음 (email_logs 기록만).
- 매 발송 즉시 email_logs 기록 → 재실행 시 자동 dedup.
- 한도(5.4.5 / daily / limit) 감지 시 즉시 abort + exit 2. 발송 간 2초 sleep.
- 템플릿: 스크립트 동일 디렉터리의 sigong_reminder.html (없으면 /tmp).

운영:
  docker exec auri-survey-api python /app/scripts/send_sigong_reminder.py --dry-run
  docker exec auri-survey-api python /app/scripts/send_sigong_reminder.py            # 실 발송

Exit codes: 0=완료, 1=설정누락, 2=한도 abort
"""
import argparse
import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, '/app')
sys.stdout.reconfigure(encoding='utf-8')

from services.db import connect, disconnect, get_db
from services.email_service import send_email
from config import get_settings


TYPE = 'sigong_reminder'
SUBJECT = '[AURI] 시공 분야 실무자 의견을 기다립니다 — 건축 AI 설문 (사례품 2만원)'


def _load_template() -> str:
    for p in (Path(__file__).parent / 'sigong_reminder.html', Path('/tmp/sigong_reminder.html')):
        if p.exists():
            return p.read_text(encoding='utf-8')
    raise FileNotFoundError('sigong_reminder.html (검색: 스크립트 디렉터리, /tmp)')


def _render(name: str, org: str, survey_url: str, tpl: str) -> str:
    return (tpl.replace('{{name}}', name or '실무자')
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
    already = set(await db.email_logs.distinct('token', {'type': TYPE, 'status': 'sent'}))
    print(f'submitted={len(submitted)} 이미 {TYPE} sent={len(already)}')

    cursor = db.participants.find(
        {
            'category': '시공',
            'email_sent': True,
            'bounced': {'$ne': True},
            'email_invalid': {'$ne': True},
            'source': {'$ne': 'staff'},
            'email': {'$exists': True, '$ne': ''},
        },
        {'_id': 0, 'token': 1, 'email': 1, 'name': 1, 'org': 1, 'category': 1},
    )
    raw = await cursor.to_list(length=20000)
    candidates = [c for c in raw if c['token'] not in submitted and c['token'] not in already]
    print(f'대상자 (시공·invite수신·미응답·미발송): {len(candidates)}')

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
        survey_url = f"{base}/?token={token}"
        html = _render(name, org, survey_url, tpl)

        if dry_run:
            print(f'[{i}/{len(candidates)}] [dry] {email} ({name})')
            continue

        log_base = {
            'batch_id': 'sigong-reminder',
            'token': token, 'email': email, 'name': name, 'org': org,
            'category': p.get('category', ''),
            'type': TYPE, 'subject': SUBJECT,
            'admin_email': 'system', 'admin_name': '시공 reminder',
            'sent_at': datetime.utcnow(),
        }
        try:
            send_email(email, SUBJECT, html)
            await db.email_logs.insert_one({**log_base, 'status': 'sent', 'error': ''})
            print(f'[{i}/{len(candidates)}] sent {email} ({name})')
            sent_n += 1
            time.sleep(2)
        except Exception as e:
            err = str(e)[:300]
            print(f'[{i}/{len(candidates)}] FAIL {email}: {err}')
            try:
                await db.email_logs.insert_one({**log_base, 'status': 'failed', 'error': err})
            except Exception:
                pass
            failed.append({'email': email, 'error': err})
            if '5.4.5' in err or 'daily' in err.lower() or 'limit exceeded' in err.lower():
                aborted = True
                remain = len(candidates) - i
                print(f'[ABORT] Gmail 한도 초과 — {sent_n} sent, {remain} 남음. 재실행 시 자동 dedup.')
                break
            time.sleep(3)

    print(f'\n=== summary === sent={sent_n} failed={len(failed)} aborted={aborted}')
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
