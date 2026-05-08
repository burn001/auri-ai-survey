"""wave{N}.json 의 sub-batch 들을 admin /email/send 에 차례로 POST.

Usage: python send_wave.py <wave_num>

1차 사이클(send_batch1.py) 패턴 그대로 — 50통/sub-batch + 120초 sleep, 결과 send_log_wave{N}.json 누적.
"""
import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone
sys.stdout.reconfigure(encoding="utf-8")

ADMIN_TOKEN = "3fa144ea17463b30fd4652a9"
API_BASE = "https://alris.ddns.net:8443/ai/api/admin"
SUBJECT = "건축 분야 AI 설문조사 참여 요청 (AURI)"
EMAIL_TYPE = "invite"
INTERVAL_SEC = 120

if len(sys.argv) < 2:
    print("Usage: python send_wave.py <wave_num>", file=sys.stderr)
    sys.exit(1)

wave_num = int(sys.argv[1])
OUT_DIR = Path(__file__).parent
wave_path = OUT_DIR / f"wave{wave_num}.json"
data = json.loads(wave_path.read_text(encoding="utf-8"))
sub_batches = data["tokens"]
log_path = OUT_DIR / f"send_log_wave{wave_num}.json"

print(f"[{datetime.now().isoformat(timespec='seconds')}] WAVE {wave_num} 시작 — {data['size']}통 / {len(sub_batches)} sub-batch", flush=True)

results = []

def post_send(tokens):
    payload = json.dumps({"tokens": tokens, "subject": SUBJECT, "type": EMAIL_TYPE}).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}/email/send",
        data=payload,
        headers={"X-Admin-Token": ADMIN_TOKEN, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))

abort_reason = None
for i, tokens in enumerate(sub_batches, 1):
    started = datetime.now(timezone.utc)
    print(f"\n[wave {wave_num} batch {i}/{len(sub_batches)}] {started.isoformat(timespec='seconds')} sending {len(tokens)} tokens...", flush=True)
    try:
        status, body = post_send(tokens)
        sent = body.get("sent", 0)
        failed = body.get("failed", 0)
        skipped = body.get("skipped", 0)
        batch_id = body.get("batch_id")
        errors = body.get("errors", [])
        ended = datetime.now(timezone.utc)
        dur = (ended - started).total_seconds()
        print(f"  status={status} batch_id={batch_id} sent={sent} failed={failed} skipped={skipped} dur={dur:.1f}s", flush=True)
        if errors:
            print(f"  errors (first 3): {errors[:3]}", flush=True)
        results.append({
            "wave": wave_num,
            "batch": i,
            "size": len(tokens),
            "started": started.isoformat(timespec="seconds"),
            "ended": ended.isoformat(timespec="seconds"),
            "duration_sec": round(dur, 1),
            "http_status": status,
            "batch_id": batch_id,
            "sent": sent,
            "failed": failed,
            "skipped": skipped,
            "errors": errors[:10],
        })
        if status != 200:
            abort_reason = f"HTTP status {status} (expected 200)"
        elif failed > 0:
            abort_reason = f"failed > 0 ({failed} failures)"
        elif sent != len(tokens):
            abort_reason = f"sent ({sent}) != tokens ({len(tokens)})"
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        print(f"  HTTPError {e.code}: {body_text[:300]}", flush=True)
        results.append({"wave": wave_num, "batch": i, "size": len(tokens), "error": f"HTTP {e.code}", "body": body_text[:500]})
        abort_reason = f"HTTPError {e.code}"
    except Exception as e:
        print(f"  Exception: {type(e).__name__}: {e}", flush=True)
        results.append({"wave": wave_num, "batch": i, "size": len(tokens), "error": f"{type(e).__name__}: {e}"})
        abort_reason = f"{type(e).__name__}: {e}"

    log_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    if abort_reason:
        print(f"\n[ABORT] {abort_reason} — checkpoint saved to {log_path.name}, halting wave.", flush=True)
        break

    if i < len(sub_batches):
        print(f"  sleeping {INTERVAL_SEC}s before next batch...", flush=True)
        time.sleep(INTERVAL_SEC)

total_sent = sum(r.get("sent", 0) for r in results)
total_failed = sum(r.get("failed", 0) for r in results)
total_skipped = sum(r.get("skipped", 0) for r in results)
print(f"\n[{datetime.now().isoformat(timespec='seconds')}] WAVE {wave_num} 완료 — sent={total_sent} failed={total_failed} skipped={total_skipped}", flush=True)
