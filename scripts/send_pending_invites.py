"""미발송 풀(wave2 + wave3)에 대한 invite 메일 dedup 발송.

run_reward_dispatch.bat의 Phase 4에서 호출.

흐름:
1. wave2.json + wave3.json 토큰 모음
2. participants에서 email_sent=True 토큰 제외 (dedup)
3. 50통씩 admin /email/send POST, 한도 초과 시 abort + exit 2

Exit codes: 0=완료, 2=한도 abort, 3=err
"""
import asyncio
import json
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

sys.path.insert(0, '/app')
sys.stdout.reconfigure(encoding='utf-8')

from services.db import connect, disconnect, get_db


ADMIN_TOKEN = '3fa144ea17463b30fd4652a9'
API_BASE = 'https://alris.ddns.net:8443/ai/api/admin'
SUBJECT = '건축 분야 AI 설문조사 참여 요청 (AURI)'
EMAIL_TYPE = 'invite'
SUB_BATCH = 50
INTERVAL_SEC = 120
WAVES = [2, 3]
SCRIPTS_DIR = Path('/app/scripts')


def post_send(tokens):
    payload = json.dumps({'tokens': tokens, 'subject': SUBJECT, 'type': EMAIL_TYPE}).encode('utf-8')
    req = urllib.request.Request(
        f'{API_BASE}/email/send', data=payload,
        headers={'X-Admin-Token': ADMIN_TOKEN, 'Content-Type': 'application/json'},
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        return resp.status, json.loads(resp.read().decode('utf-8'))


async def main() -> int:
    await connect()
    db = get_db()

    all_tokens = []
    for wn in WAVES:
        wp = SCRIPTS_DIR / f'wave{wn}.json'
        if not wp.exists():
            print(f'[skip] wave{wn}.json 없음')
            continue
        data = json.loads(wp.read_text(encoding='utf-8'))
        for sub in data.get('tokens', []):
            all_tokens.extend(sub)

    if not all_tokens:
        print('[OK] wave 큐가 비었음 — 발송 대상 없음')
        await disconnect()
        return 0

    sent_tokens = set(await db.participants.distinct(
        'token', {'token': {'$in': all_tokens}, 'email_sent': True}
    ))
    bounced = set(await db.participants.distinct(
        'token', {'token': {'$in': all_tokens},
                  '$or': [{'bounced': True}, {'email_invalid': True}]}
    ))
    pending = [t for t in all_tokens if t not in sent_tokens and t not in bounced]

    print(f'all={len(all_tokens)} already_sent={len(sent_tokens)} bounced/invalid={len(bounced)} pending={len(pending)}', flush=True)

    if not pending:
        print('[OK] 모든 wave 토큰 발송 완료 — full completion')
        await disconnect()
        return 0

    sent_total = 0
    failed_total = 0
    skipped_total = 0
    aborted = False

    for i in range(0, len(pending), SUB_BATCH):
        batch = pending[i:i + SUB_BATCH]
        idx = i // SUB_BATCH + 1
        nbatch = (len(pending) + SUB_BATCH - 1) // SUB_BATCH
        started = datetime.now()
        print(f'\n[batch {idx}/{nbatch}] {started.isoformat(timespec="seconds")} sending {len(batch)} tokens...', flush=True)

        try:
            status, body = post_send(batch)
            sent = body.get('sent', 0)
            failed = body.get('failed', 0)
            skipped = body.get('skipped', 0)
            errors = body.get('errors', [])
            sent_total += sent
            failed_total += failed
            skipped_total += skipped
            print(f'  status={status} sent={sent} failed={failed} skipped={skipped} (cum sent={sent_total} failed={failed_total})', flush=True)
            if errors:
                print(f'  errors (first 3): {errors[:3]}', flush=True)
            if status != 200 or failed > 0 or sent < len(batch) - skipped:
                aborted = True
                print(f'[ABORT] partial/failure — Gmail 한도 추정. 다음 실행 시 dedup으로 자동 이어집니다.', flush=True)
                break
        except urllib.error.HTTPError as e:
            body_text = e.read().decode('utf-8', errors='replace')
            print(f'  HTTPError {e.code}: {body_text[:300]}', flush=True)
            aborted = True
            break
        except Exception as e:
            print(f'  Exception: {type(e).__name__}: {e}', flush=True)
            aborted = True
            break

        if i + SUB_BATCH < len(pending):
            print(f'  sleeping {INTERVAL_SEC}s before next batch...', flush=True)
            time.sleep(INTERVAL_SEC)

    print(f'\n=== summary === sent={sent_total} failed={failed_total} skipped={skipped_total} aborted={aborted}', flush=True)
    await disconnect()
    return 2 if aborted else 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
