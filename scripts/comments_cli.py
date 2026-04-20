"""
review_comments 스레드 조작 CLI — 개발자/연구진이 어드민 페이지 없이 터미널에서
코멘트 조회·답글·상태 변경을 빠르게 처리하기 위한 도구.

DB에 직접 접근하므로 백엔드 API 인증을 우회한다. --as 옵션으로 author를 지정;
지정 안 하면 admins 컬렉션의 owner 1순위 자동 선택.

사용 예:
    # 전체 스레드 요약 (qid별 카운트)
    python scripts/comments_cli.py list

    # status=open만 (작업 큐)
    python scripts/comments_cli.py open

    # 특정 qid 스레드 트리
    python scripts/comments_cli.py show Q17_2

    # 답글 (parent comment_id 지정 — qid는 자동 추론)
    python scripts/comments_cli.py reply <comment_id> "다음 배포에 반영했습니다"

    # 신규 관리자 코멘트
    python scripts/comments_cli.py post Q17_2 "이 부분 다시 검토 필요"

    # 상태 변경
    python scripts/comments_cli.py status <comment_id> resolved

    # 코멘트 삭제 (답글 있으면 soft-delete)
    python scripts/comments_cli.py delete <comment_id>

기본 author는 admins 컬렉션의 첫 owner. --as <email>로 지정 가능.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pymongo import MongoClient

STATUSES = {"open", "in_review", "resolved", "rejected"}
STATUS_ICON = {"open": "📌", "in_review": "🟡", "resolved": "🟢", "rejected": "⚪"}
STATUS_LABEL = {"open": "열림", "in_review": "검토중", "resolved": "반영완료", "rejected": "보류"}


def make_db(args):
    client = MongoClient(args.uri)
    return client[args.db]


def resolve_admin(db, email_hint: str | None) -> dict:
    if email_hint:
        admin = db.admins.find_one({"email": email_hint.lower(), "active": True})
        if not admin:
            sys.exit(f"admins 컬렉션에서 {email_hint} 찾을 수 없음 (active=True 조건)")
        return admin
    admin = db.admins.find_one({"role": "owner", "active": True}, sort=[("created_at", 1)])
    if not admin:
        admin = db.admins.find_one({"active": True}, sort=[("created_at", 1)])
    if not admin:
        sys.exit("admins 컬렉션이 비어있음. --as 또는 admin 추가 필요.")
    return admin


def fmt_dt(dt):
    if not dt:
        return "-"
    if isinstance(dt, str):
        return dt
    return dt.strftime("%Y-%m-%d %H:%M")


def fmt_status(s):
    return f"{STATUS_ICON.get(s, '?')} {STATUS_LABEL.get(s, s)}"


def cmd_list(db, args):
    pipeline = [
        {"$match": {"survey_version": args.survey_version}},
        {"$group": {
            "_id": "$qid",
            "total": {"$sum": 1},
            "open": {"$sum": {"$cond": [{"$eq": ["$status", "open"]}, 1, 0]}},
            "in_review": {"$sum": {"$cond": [{"$eq": ["$status", "in_review"]}, 1, 0]}},
            "resolved": {"$sum": {"$cond": [{"$eq": ["$status", "resolved"]}, 1, 0]}},
            "rejected": {"$sum": {"$cond": [{"$eq": ["$status", "rejected"]}, 1, 0]}},
            "last": {"$max": "$created_at"},
        }},
        {"$sort": {"last": -1}},
    ]
    results = list(db.review_comments.aggregate(pipeline))
    if not results:
        print("등록된 코멘트가 없습니다.")
        return

    print(f"{'qid':<10} {'총':>3} {'📌열림':>6} {'🟡검토':>6} {'🟢반영':>6} {'⚪보류':>6}  최근")
    print("-" * 70)
    for r in results:
        print(f"{r['_id']:<10} {r['total']:>3} {r['open']:>6} {r['in_review']:>6} {r['resolved']:>6} {r['rejected']:>6}  {fmt_dt(r['last'])}")
    grand = sum(r["total"] for r in results)
    open_cnt = sum(r["open"] for r in results)
    print("-" * 70)
    print(f"전체 {grand}건 (열림 {open_cnt}건)")


def cmd_open(db, args):
    cursor = db.review_comments.find(
        {"survey_version": args.survey_version, "status": "open"},
        {"_id": 0},
    ).sort("created_at", 1)
    items = list(cursor)
    if not items:
        print("열림 상태 코멘트 없음.")
        return
    print(f"열림 상태 코멘트 {len(items)}건:\n")
    for c in items:
        print_comment(c, indent=0, show_id=True)


def cmd_show(db, args):
    qid = args.qid
    cursor = db.review_comments.find(
        {"survey_version": args.survey_version, "qid": qid},
        {"_id": 0},
    ).sort("created_at", 1)
    items = list(cursor)
    if not items:
        print(f"qid={qid} 의 코멘트 없음.")
        return

    by_id = {c["id"]: dict(c, children=[]) for c in items}
    roots = []
    for c in by_id.values():
        if c.get("parent_id") and c["parent_id"] in by_id:
            by_id[c["parent_id"]]["children"].append(c)
        else:
            roots.append(c)

    print(f"== qid={qid} 스레드 ({len(items)}건) ==\n")

    def walk(node, depth):
        print_comment(node, indent=depth * 2, show_id=True)
        for ch in node["children"]:
            walk(ch, depth + 1)

    for r in roots:
        walk(r, 0)


def print_comment(c, indent=0, show_id=False):
    pad = " " * indent
    role = "관리자" if c["author_role"] == "admin" else "연구진"
    head = f"{pad}{fmt_status(c['status'])} [{role}] {c['author_name']} ({c.get('author_org', '')}) · {fmt_dt(c['created_at'])}"
    if c.get("updated_at"):
        head += f" (수정 {fmt_dt(c['updated_at'])})"
    print(head)
    if show_id:
        print(f"{pad}  id: {c['id']}")
    text = c["text"]
    for line in text.split("\n"):
        print(f"{pad}  {line}")
    if c.get("status_changed_by"):
        print(f"{pad}  └ 상태 변경: {fmt_dt(c.get('status_changed_at'))} by {c['status_changed_by']}")
    print()


def cmd_post(db, args):
    admin = resolve_admin(db, args.as_email)
    text = args.text.strip()
    if not text:
        sys.exit("내용이 비어있습니다.")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    doc = {
        "id": uuid4().hex,
        "survey_version": args.survey_version,
        "qid": args.qid,
        "author_role": "admin",
        "author_token": admin.get("token", ""),
        "author_name": admin.get("name", ""),
        "author_email": admin.get("email", ""),
        "author_org": "관리자",
        "text": text,
        "status": "open",
        "parent_id": None,
        "created_at": now,
        "updated_at": None,
        "status_changed_at": None,
        "status_changed_by": "",
    }
    db.review_comments.insert_one(doc)
    print(f"✅ 신규 코멘트 등록 (id={doc['id']})")
    print_comment(doc, indent=0, show_id=False)


def cmd_reply(db, args):
    admin = resolve_admin(db, args.as_email)
    parent = db.review_comments.find_one({"id": args.comment_id})
    if not parent:
        sys.exit(f"comment_id={args.comment_id} 찾을 수 없음")
    text = args.text.strip()
    if not text:
        sys.exit("내용이 비어있습니다.")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    doc = {
        "id": uuid4().hex,
        "survey_version": parent["survey_version"],
        "qid": parent["qid"],
        "author_role": "admin",
        "author_token": admin.get("token", ""),
        "author_name": admin.get("name", ""),
        "author_email": admin.get("email", ""),
        "author_org": "관리자",
        "text": text,
        "status": "open",
        "parent_id": parent["id"],
        "created_at": now,
        "updated_at": None,
        "status_changed_at": None,
        "status_changed_by": "",
    }
    db.review_comments.insert_one(doc)
    print(f"✅ 답글 등록 (id={doc['id']}, qid={doc['qid']}, parent={parent['id']})")
    print_comment(doc, indent=2, show_id=False)


def cmd_status(db, args):
    admin = resolve_admin(db, args.as_email)
    if args.new_status not in STATUSES:
        sys.exit(f"허용되지 않은 상태: {args.new_status}. 가능한 값: {sorted(STATUSES)}")
    target = db.review_comments.find_one({"id": args.comment_id})
    if not target:
        sys.exit(f"comment_id={args.comment_id} 찾을 수 없음")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.review_comments.update_one(
        {"id": args.comment_id},
        {"$set": {
            "status": args.new_status,
            "status_changed_at": now,
            "status_changed_by": admin.get("name", admin.get("email", "")),
        }},
    )
    updated = db.review_comments.find_one({"id": args.comment_id}, {"_id": 0})
    print(f"✅ 상태 변경: {target['status']} → {args.new_status}")
    print_comment(updated, indent=0, show_id=False)


def cmd_delete(db, args):
    target = db.review_comments.find_one({"id": args.comment_id})
    if not target:
        sys.exit(f"comment_id={args.comment_id} 찾을 수 없음")
    has_replies = db.review_comments.count_documents({"parent_id": args.comment_id}) > 0
    if has_replies:
        db.review_comments.update_one(
            {"id": args.comment_id},
            {"$set": {
                "text": "(CLI에서 삭제된 코멘트)",
                "updated_at": datetime.now(timezone.utc).replace(tzinfo=None),
            }},
        )
        print(f"⚠ 답글이 있어 soft-delete 처리됨 (id={args.comment_id})")
    else:
        db.review_comments.delete_one({"id": args.comment_id})
        print(f"🗑 삭제 완료 (id={args.comment_id})")


def main():
    ap = argparse.ArgumentParser(description="review_comments 조작 CLI")
    ap.add_argument("--uri", default=os.getenv("MONGODB_URI", "mongodb://localhost:27017"))
    ap.add_argument("--db", default=os.getenv("MONGODB_DB", "auri_survey"))
    ap.add_argument("--survey-version", default="v7")
    ap.add_argument("--as", dest="as_email", default=os.getenv("AS_EMAIL"),
                    help="author로 사용할 admin 이메일 (기본: 첫 owner)")

    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="qid별 코멘트 카운트")
    sub.add_parser("open", help="status=open인 코멘트만 (작업 큐)")

    sp = sub.add_parser("show", help="특정 qid 스레드 트리")
    sp.add_argument("qid")

    sp = sub.add_parser("post", help="새 관리자 코멘트")
    sp.add_argument("qid")
    sp.add_argument("text")

    sp = sub.add_parser("reply", help="기존 코멘트에 답글")
    sp.add_argument("comment_id")
    sp.add_argument("text")

    sp = sub.add_parser("status", help="상태 변경")
    sp.add_argument("comment_id")
    sp.add_argument("new_status", choices=sorted(STATUSES))

    sp = sub.add_parser("delete", help="코멘트 삭제 (답글 있으면 soft)")
    sp.add_argument("comment_id")

    args = ap.parse_args()
    db = make_db(args)

    handlers = {
        "list": cmd_list, "open": cmd_open, "show": cmd_show,
        "post": cmd_post, "reply": cmd_reply,
        "status": cmd_status, "delete": cmd_delete,
    }
    handlers[args.cmd](db, args)


if __name__ == "__main__":
    main()
