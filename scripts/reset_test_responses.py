"""Test 응답 데이터 일괄 삭제.

설문지 v7 → v11 전환에 따라 기존 v7 형식 응답을 비우고 v11 본 운영을 시작할 때 사용.
기본 동작: responses 컬렉션만 삭제 (가장 보수적). 옵션으로 자가등록 참가자·코멘트·이메일
로그·백업까지 함께 정리할 수 있다. 모든 삭제는 dry-run 후 확인 프롬프트로 진행.

Usage:
    # 응답만 삭제 (기본)
    python scripts/reset_test_responses.py

    # 응답 + 자가등록 참가자 + 검토 코멘트 + 이메일 로그까지 전부 정리
    python scripts/reset_test_responses.py --all

    # 옵션 개별 지정
    python scripts/reset_test_responses.py --self-participants --comments --emails

    # 확인 프롬프트 스킵 (CI/스크립트에서 실행 시)
    python scripts/reset_test_responses.py --all --yes
"""
import argparse
import os
import sys
from pymongo import MongoClient


def main():
    ap = argparse.ArgumentParser(description="auri-ai-survey test 응답 데이터 일괄 삭제")
    ap.add_argument("--uri", default=os.getenv("MONGODB_URI", "mongodb://localhost:27017"))
    ap.add_argument("--db", default=os.getenv("MONGODB_DB", "auri_survey"))
    ap.add_argument("--self-participants", action="store_true",
                    help="자가등록 참가자(participants 중 source=self) 삭제")
    ap.add_argument("--comments", action="store_true",
                    help="review_comments 전체 삭제")
    ap.add_argument("--emails", action="store_true",
                    help="email_logs 전체 삭제")
    ap.add_argument("--backups", action="store_true",
                    help="participants_backup 전체 삭제")
    ap.add_argument("--all", action="store_true",
                    help="self-participants + comments + emails + backups 모두 적용")
    ap.add_argument("--yes", action="store_true", help="확인 프롬프트 스킵")
    args = ap.parse_args()

    if args.all:
        args.self_participants = args.comments = args.emails = args.backups = True

    client = MongoClient(args.uri)
    db = client[args.db]

    plan = []
    plan.append(("db.responses (전체)", db.responses.count_documents({})))
    if args.self_participants:
        plan.append(("db.participants (source=self)",
                     db.participants.count_documents({"source": "self"})))
    if args.comments:
        plan.append(("db.review_comments (전체)", db.review_comments.count_documents({})))
    if args.emails:
        plan.append(("db.email_logs (전체)", db.email_logs.count_documents({})))
    if args.backups:
        plan.append(("db.participants_backup (전체)",
                     db.participants_backup.count_documents({})))

    print(f"\nDB: {args.db} @ {args.uri}\n")
    print("삭제 계획:")
    for label, n in plan:
        print(f"  - {label}: {n}건")

    # 보존되는 항목 안내
    if not args.self_participants:
        kept = db.participants.count_documents({"source": "self"})
        if kept:
            print(f"\n⚠ 자가등록 참가자 {kept}건은 그대로 유지됩니다 (--self-participants로 함께 삭제 가능).")
    if not args.emails:
        n = db.email_logs.count_documents({})
        if n:
            print(f"⚠ email_logs {n}건은 그대로 유지됩니다 (--emails로 함께 삭제 가능).")

    if not args.yes:
        try:
            ok = input("\n위 계획대로 진행하시겠습니까? (yes/no): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n취소됨"); sys.exit(1)
        if ok not in ("y", "yes"):
            print("취소됨"); sys.exit(0)

    print()
    r = db.responses.delete_many({})
    print(f"deleted responses: {r.deleted_count}")
    if args.self_participants:
        r = db.participants.delete_many({"source": "self"})
        print(f"deleted self-registered participants: {r.deleted_count}")
    if args.comments:
        r = db.review_comments.delete_many({})
        print(f"deleted review_comments: {r.deleted_count}")
    if args.emails:
        r = db.email_logs.delete_many({})
        print(f"deleted email_logs: {r.deleted_count}")
    if args.backups:
        r = db.participants_backup.delete_many({})
        print(f"deleted participants_backup: {r.deleted_count}")

    print("\n완료.")
    client.close()


if __name__ == "__main__":
    main()
