"""분야별 응답 현황을 Telegram으로 보고.

야간 배포 schtask(`\\auri-survey-deploy-YYYY-MM-DD`) Phase 2에서 호출.
check_response_by_category.py의 집계 함수를 그대로 재사용해서 텔레그램 친화적인
한 줄짜리 텍스트로 포맷한다.

환경변수:
- TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  (없으면 조용히 종료)
- MONGODB_URI, MONGODB_DB  (없으면 컨테이너 기본값 사용)
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pymongo import MongoClient

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from check_response_by_category import by_category, by_source, rate

KST = timezone(timedelta(hours=9))
DEFAULT_URI = "mongodb://alrisAdmin:5kBh10AQc3QihNgbUajOVdQSq3pMtq5b@mongod:27017/?authSource=admin&directConnection=true"


def _send_telegram(bot: str, chat: str, text: str) -> bool:
    url = f"https://api.telegram.org/bot{bot}/sendMessage"
    payload = json.dumps({"chat_id": chat, "text": text, "disable_web_page_preview": True}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"[TELEGRAM] HTTP {resp.status}")
            return resp.status == 200
    except Exception as e:
        print(f"[TELEGRAM] FAIL: {e}")
        return False


def main() -> int:
    bot = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not bot or not chat:
        print("[TELEGRAM] env 없음 — skip")
        return 0

    uri = os.environ.get("MONGODB_URI", DEFAULT_URI)
    dbname = os.environ.get("MONGODB_DB", "auri_survey")
    client = MongoClient(uri)
    db = client[dbname]

    cat = by_category(db)
    src = by_source(db)

    total_sub = sum(r["submitted"] for r in cat)
    total_sent = sum(r["sent"] for r in cat)
    total_pool = sum(r["pool"] for r in cat)

    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    lines = [
        "📊 [AURI 설문] 분야별 응답 현황",
        f"시각: {now_kst}",
        "",
        f"누적 응답: {total_sub} / 발송 {total_sent} (풀 {total_pool})",
        f"발송 대비 응답률: {rate(total_sub, total_sent):.1f}%",
        "",
        "— 분야별 (응답 / 발송 / 응답률)",
    ]
    for r in cat:
        if r["submitted"] == 0 and r["sent"] == 0:
            continue
        label = str(r["_id"])
        lines.append(f"   {label:<6s}  {r['submitted']:>4d} / {r['sent']:>4d}  ({rate(r['submitted'], r['sent']):.1f}%)")

    lines.append("")
    lines.append("— 출처별 (응답 / 발송)")
    for r in src:
        if r["sent"] == 0 and r["submitted"] == 0:
            continue
        label = str(r["_id"])[:18]
        lines.append(f"   {label:<18s}  {r['submitted']:>4d} / {r['sent']:>4d}")

    text = "\n".join(lines)
    print(text)
    print("---")
    _send_telegram(bot, chat, text)

    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
