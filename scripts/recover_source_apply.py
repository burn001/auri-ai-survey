"""참가자 source·started_at 복구 적용 — 실수로 to_staff_cleanup 된 토큰 원복.

source 후보를 org 부분일치로 자동 결정(법인회원사 우선, 없으면 일반회원)하거나
--source 로 직접 지정. started_at 은 --started-at(ISO) 로 복원.
db_safety: 기본 dry-run. 실제 적용은 --apply 필수.

사용:
  # 진단(dry-run)
  docker exec auri-survey-api python /app/scripts/recover_source_apply.py <token> \
      --org-hint 동원 --started-at 2026-05-20T01:08:01.562000
  # 적용
  ... --apply
  # source 직접 지정
  ... --source "FM학회 회원" --apply
"""
import asyncio
import sys
from datetime import datetime

sys.path.insert(0, "/app")
from services.db import connect, get_db


def _arg(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


async def decide_source(d, org_hint):
    """org 부분일치로 source 후보 결정. 법인회원사 우선, 없으면 일반회원."""
    for src in ("FM학회 법인회원사 담당자", "FM학회 회원"):
        cur = d.participants.aggregate([
            {"$match": {"source": src, "org": {"$regex": org_hint}}},
            {"$project": {"_id": 0, "email": 1, "name": 1, "org": 1}},
            {"$limit": 5},
        ])
        hits = [r async for r in cur]
        if hits:
            print(f"  source='{src}' org~/{org_hint}/ 일치 {len(hits)}건 (예: {hits[0]})")
            return src
    return "FM학회 회원"


async def main():
    if len(sys.argv) < 2 or sys.argv[1].startswith("--"):
        print("usage: recover_source_apply.py <token> [--source S | --org-hint O] [--started-at ISO] [--apply]")
        return
    token = sys.argv[1]
    source = _arg("--source")
    org_hint = _arg("--org-hint")
    started_at_raw = _arg("--started-at")
    apply = "--apply" in sys.argv

    await connect()
    d = get_db()

    if not source:
        if not org_hint:
            print("--source 또는 --org-hint 중 하나 필요")
            return
        print(f"=== org~/{org_hint}/ 로 source 추정 ===")
        source = await decide_source(d, org_hint)
    print(f"\n=== 복구 source: '{source}' ===")

    set_doc = {"source": source}
    if started_at_raw:
        set_doc["started_at"] = datetime.fromisoformat(started_at_raw)

    if not apply:
        print(f"\n[DRY-RUN] --apply 필요. 적용 예정 필드: {set_doc}")
        return

    res = await d.participants.update_one({"token": token}, {"$set": set_doc})
    print(f"\nrecovery: matched={res.matched_count} modified={res.modified_count}")

    p = await d.participants.find_one({"token": token})
    print(f"\n=== 복구 후 state ===")
    for k in ["source", "started_at", "consent_reward", "reward_phone", "email_release_phase"]:
        print(f"  {k}: {p.get(k)}")


if __name__ == "__main__":
    asyncio.run(main())
