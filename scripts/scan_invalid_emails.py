"""participants 전체에서 RFC 5321 위반 가능성 있는 email 일괄 검출 + email_invalid 표시.

검사 패턴:
- @ 없음 (전화번호 등)
- comma in local/domain
- whitespace / 괄호 / (at) 우회 표기
- @ 두 번 이상
- 끝/시작 dot

상세 미세 케이스는 살리고 명백한 invalid만 처리 — 의심 시 검토용 dry-run 우선.
"""
import asyncio, sys, re
sys.path.insert(0, '/app')
from services.db import connect, disconnect, get_db
from datetime import datetime, timezone

DRY_RUN = '--apply' not in sys.argv  # 기본 dry-run

# 정상 RFC 5321 약식: local@domain, local: [\w.\-+], domain: [\w.\-]\.\w{2,}
SANE = re.compile(r'^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$')

INVALID_HINTS = [
    ('comma', re.compile(r',')),
    ('whitespace', re.compile(r'\s')),
    ('paren_at', re.compile(r'\(at\)|\(AT\)|\[at\]', re.I)),
    ('paren_dot', re.compile(r'\(dot\)|\(DOT\)|\[dot\]', re.I)),
    ('no_at', re.compile(r'^[^@]+$')),
    ('multi_at', re.compile(r'@.*@')),
    ('lead_dot', re.compile(r'^\.')),
    ('trail_dot', re.compile(r'\.$')),
]

async def main():
    await connect()
    db = get_db()
    cursor = db.participants.find({}, {'token':1,'name':1,'email':1,'email_invalid':1,'_id':0})
    suspects = []
    async for p in cursor:
        email = (p.get('email') or '').strip()
        if not email:
            continue
        if p.get('email_invalid'):
            continue  # 이미 표시됨
        if SANE.match(email):
            continue
        reasons = [name for name, pat in INVALID_HINTS if pat.search(email)]
        if not reasons:
            reasons = ['unmatched_sane_regex']
        suspects.append({'token': p['token'], 'name': p.get('name'), 'email': email, 'reasons': reasons})

    print(f'suspects: {len(suspects)}', flush=True)
    for s in suspects:
        print(f"  {s['token']}  {s['name']!r:20}  {s['email']!r:40}  reasons={s['reasons']}", flush=True)

    if DRY_RUN:
        print('\n[DRY-RUN] --apply 플래그 없음. 표시 없이 종료.', flush=True)
    else:
        tokens = [s['token'] for s in suspects]
        if tokens:
            r = await db.participants.update_many(
                {'token': {'$in': tokens}},
                {'$set': {'email_invalid': True,
                          'email_invalid_at': datetime.now(timezone.utc),
                          'email_invalid_reason': 'auto-scan: RFC 5321 violation'}}
            )
            print(f'\n[APPLIED] matched={r.matched_count} modified={r.modified_count}', flush=True)
        else:
            print('\n[OK] 더 표시할 invalid 없음', flush=True)

    await disconnect()

asyncio.run(main())
