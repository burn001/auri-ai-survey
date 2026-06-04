"""직군 라우팅 오류로 PART III 응답이 누락된 응답자에게 보완 안내 메일 발송.

대상 (모두 충족):
- responses.submitted_at != null
- responses.Q6 ∈ {0,1,2,3} 인데 해당 직군 PART III 분기 3문항 중 1건 이상 누락
- participants.consent_reward == true  ← 사용자 명시 요청
- participants.reward_phone 비어있지 않음
- participants.source != 'staff' & category != '연구진'
- 이미 fill_missing_notice 메일 받지 않은 토큰 (email_logs dedup)

운영 (winserver에서):
  docker exec -i auri-survey-api tee /tmp/fill_missing.py < scripts/send_fill_missing_notice.py
  docker exec auri-survey-api python /tmp/fill_missing.py --dry-run
  docker exec auri-survey-api python /tmp/fill_missing.py             # 실 발송

페이스: 발송 간 2초 sleep, 한도 초과 (5.4.5 / daily / limit) 재감지 시 abort.
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


Q6_TO_PART3_QIDS = {
    0: ('QA1', 'QA2', 'QA3'),
    1: ('QB1', 'QB2', 'QB3'),
    2: ('QC1', 'QC2', 'QC3'),
    3: ('QD1', 'QD2', 'QD3'),
}

# 보완 응답 마감일 — 메일 본문에 노출. 사례품 발송 조건.
DEADLINE_LABEL = '2026-05-25(일) 24시'


def _load_template() -> str:
    p = Path('/app/templates/fill_missing_notice.html')
    return p.read_text(encoding='utf-8')


def _render(name: str, org: str, fill_url: str, deadline: str, tpl: str) -> str:
    return (tpl.replace('{{name}}', name or '응답자')
               .replace('{{org}}', org or '')
               .replace('{{fill_url}}', fill_url)
               .replace('{{deadline}}', deadline))


def _is_missing(resp: dict, qid: str) -> bool:
    v = resp.get(qid)
    return v is None or (isinstance(v, list) and len(v) == 0)


async def main(dry_run: bool, limit: int) -> int:
    s = get_settings()
    if not s.GMAIL_USER or not s.GMAIL_APP_PASSWORD:
        print('[abort] GMAIL_USER/GMAIL_APP_PASSWORD 환경변수 없음')
        return 1

    tpl = _load_template()
    await connect()
    db = get_db()

    # 1) 이미 fill_missing_notice 받은 토큰 (dedup)
    already = set(await db.email_logs.distinct(
        'token', {'type': 'fill_missing_notice', 'status': 'sent'}
    ))
    print(f'이미 fill_missing_notice sent: {len(already)}')

    # 2) 제출 완료자 중 PART III 누락 + consent_reward + reward_phone 조건 만족 후보 식별
    submitted_cursor = db.responses.find({'submitted_at': {'$ne': None}}, {'token': 1, 'responses': 1, '_id': 0})
    submitted = await submitted_cursor.to_list(length=20000)
    print(f'submitted 응답: {len(submitted)}')

    candidate_tokens = []
    response_by_token = {}
    for r in submitted:
        token = r['token']
        if token in already:
            continue
        resp = r.get('responses') or {}
        q6 = resp.get('Q6')
        try:
            q6_idx = int(q6) if q6 is not None else None
        except (TypeError, ValueError):
            continue
        if q6_idx not in Q6_TO_PART3_QIDS:
            continue
        missing = [qid for qid in Q6_TO_PART3_QIDS[q6_idx] if _is_missing(resp, qid)]
        if not missing:
            continue
        candidate_tokens.append(token)
        response_by_token[token] = missing

    print(f'PART III 누락 후보: {len(candidate_tokens)}')
    if not candidate_tokens:
        print('대상자가 없습니다. 종료.')
        await disconnect()
        return 0

    # 3) participants 조인 + 사례품 동의 + 전화번호 보유 필터
    cursor = db.participants.find(
        {
            'token': {'$in': candidate_tokens},
            'source': {'$ne': 'staff'},
            'category': {'$ne': '연구진'},
            'consent_reward': True,
            'reward_phone': {'$exists': True, '$nin': ['', None]},
            'email': {'$exists': True, '$nin': ['', None]},
        },
        {'_id': 0, 'token': 1, 'email': 1, 'name': 1, 'org': 1, 'category': 1},
    )
    candidates = await cursor.to_list(length=20000)
    print(f'발송 대상 (consent_reward + 전화번호 + 이메일): {len(candidates)}')

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
        missing_qids = response_by_token.get(token, [])
        fill_url = f"{base}/?token={token}&fill=1"
        subject = '[AURI 건축AI 실무자 조사] 누락된 직군 특화 3문항 보완 응답 요청 (사례품 발송 관련)'
        html = _render(name, org, fill_url, DEADLINE_LABEL, tpl)

        if dry_run:
            print(f'[{i}/{len(candidates)}] [dry] {email} ({name} / {category}) missing={",".join(missing_qids)}')
            continue

        try:
            send_email(email, subject, html)
            await db.email_logs.insert_one({
                'batch_id': 'fill-missing-notice',
                'token': token,
                'email': email,
                'name': name,
                'org': org,
                'category': category,
                'type': 'fill_missing_notice',
                'subject': subject,
                'admin_email': 'system',
                'admin_name': '누락 항목 보완 안내',
                'sent_at': datetime.utcnow(),
                'status': 'sent',
                'error': '',
                'missing_qids_snapshot': missing_qids,
                'deadline_label': DEADLINE_LABEL,
            })
            print(f'[{i}/{len(candidates)}] sent {email} ({name})')
            sent_n += 1
            time.sleep(2)
        except Exception as e:
            err = str(e)[:300]
            print(f'[{i}/{len(candidates)}] FAIL {email}: {err}')
            try:
                await db.email_logs.insert_one({
                    'batch_id': 'fill-missing-notice',
                    'token': token,
                    'email': email,
                    'name': name,
                    'org': org,
                    'category': category,
                    'type': 'fill_missing_notice',
                    'subject': subject,
                    'admin_email': 'system',
                    'admin_name': '누락 항목 보완 안내',
                    'sent_at': datetime.utcnow(),
                    'status': 'failed',
                    'error': err,
                    'missing_qids_snapshot': missing_qids,
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
