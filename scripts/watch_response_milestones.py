"""응답자 수 milestone 도달 시 Telegram 보고 데몬.

설계:
- POLL_INTERVAL_SEC 마다 db.responses.countDocuments({submitted_at!=null}) 조회
- 직전 보고 milestone(`dispatch_meta.response_milestone_watcher.last_milestone`)와 비교
- 새 milestone(= last + step) 도달 시 텔레그램 발송 + DB 갱신
- 한 사이클에 여러 milestone 한 번에 통과 시 모두 보고 (catch-up)
- 첫 실행이면 현재 count의 floor(count/step)*step으로 초기화 (소급 발송 X)

환경:
- MONGODB_URI / MONGODB_DB
- TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID  (없으면 1초 후 exit 1 — restart 정책으로 무한 재시작 방지)
- MILESTONE_STEP (기본 10)
- POLL_INTERVAL_SEC (기본 60)
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pymongo import MongoClient

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from check_response_by_category import by_category, rate

KST = timezone(timedelta(hours=9))
STATE_DOC_ID = "response_milestone_watcher"
DEFAULT_URI = "mongodb://alrisAdmin:5kBh10AQc3QihNgbUajOVdQSq3pMtq5b@mongod:27017/?authSource=admin&directConnection=true"


def log(msg: str) -> None:
    print(f"[{datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def send_telegram(bot: str, chat: str, text: str) -> bool:
    url = f"https://api.telegram.org/bot{bot}/sendMessage"
    payload = json.dumps({"chat_id": chat, "text": text, "disable_web_page_preview": True}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception as e:
        log(f"[TELEGRAM] FAIL: {e}")
        return False


def get_submitted_count(db) -> int:
    return db.responses.count_documents({"submitted_at": {"$ne": None}})


def get_last_milestone(db):
    doc = db.dispatch_meta.find_one({"_id": STATE_DOC_ID})
    return doc.get("last_milestone") if doc else None


def set_last_milestone(db, value: int) -> None:
    db.dispatch_meta.update_one(
        {"_id": STATE_DOC_ID},
        {"$set": {"last_milestone": value, "updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


def build_message(db, milestone: int, current_total: int) -> str:
    cat = by_category(db)
    lines = [
        f"🔔 [AURI 설문] 응답 {milestone}건 돌파 (현재 {current_total})",
        f"시각: {datetime.now(KST).strftime('%Y-%m-%d %H:%M KST')}",
        "",
        "— 분야별 (응답 / 발송 / 응답률)",
    ]
    for r in cat:
        if r["submitted"] == 0 and r["sent"] == 0:
            continue
        label = str(r["_id"])
        lines.append(f"   {label:<6s}  {r['submitted']:>4d} / {r['sent']:>4d}  ({rate(r['submitted'], r['sent']):.1f}%)")
    return "\n".join(lines)


def main() -> int:
    bot = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not bot or not chat:
        log("[WATCHER] TELEGRAM env 없음 — 종료")
        time.sleep(1)
        return 1

    uri = os.environ.get("MONGODB_URI", DEFAULT_URI)
    dbname = os.environ.get("MONGODB_DB", "auri_survey")
    step = int(os.environ.get("MILESTONE_STEP", "10"))
    poll = int(os.environ.get("POLL_INTERVAL_SEC", "60"))

    client = MongoClient(uri)
    db = client[dbname]

    last = get_last_milestone(db)
    if last is None:
        current = get_submitted_count(db)
        last = (current // step) * step
        set_last_milestone(db, last)
        log(f"[WATCHER] 초기 milestone={last} (current={current}, step={step}) — 소급 발송 없음")

    log(f"[WATCHER] start: poll={poll}s step={step} last_milestone={last}")

    while True:
        try:
            current = get_submitted_count(db)
            next_ms = last + step
            while current >= next_ms:
                msg = build_message(db, next_ms, current)
                if send_telegram(bot, chat, msg):
                    log(f"[WATCHER] milestone {next_ms} 보고 완료 (current={current})")
                    last = next_ms
                    set_last_milestone(db, last)
                    next_ms = last + step
                else:
                    log(f"[WATCHER] telegram 실패 — 다음 cycle 재시도")
                    break
        except Exception as e:
            log(f"[WATCHER] cycle error: {e}")
        time.sleep(poll)


if __name__ == "__main__":
    sys.exit(main())
