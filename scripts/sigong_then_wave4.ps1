# 시공 reminder 잔여를 우선 완주시킨 뒤, 남는 Gmail quota로 wave4를 발송.
# 배경: Gmail 한도는 rolling 24h. 시공 스크립트는 2초 블라스트라 quota 벽에서 abort(exit 2)되므로,
#       quota가 풀리는 속도(어제 정오분 roll-off)에 맞춰 재시도 루프로 드레인한다.
# 두 스크립트 모두 email_logs dedup → 재실행 안전. 한도 시 abort.
# 실행: Start-Process로 detached 기동 (SSH 끊겨도 생존).

$ErrorActionPreference = 'Continue'
$log = 'D:\docker\auri-ai-survey\logs\sigong-then-wave4.log'
function L($m) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $m" | Out-File -Append -Encoding utf8 $log }

L '=== orchestrator start ==='

# Phase A: 시공 reminder 잔여 드레인 (exit 0 = 전량 완료까지 재시도)
$max = 40
for ($i = 1; $i -le $max; $i++) {
    L "[sigong] attempt $i"
    docker exec auri-survey-api python /app/scripts/send_sigong_reminder.py *>> $log
    $rc = $LASTEXITCODE
    L "[sigong] attempt $i rc=$rc"
    if ($rc -eq 0) { L '[sigong] COMPLETE'; break }
    Start-Sleep -Seconds 240
}

# Phase B: 남는 quota로 wave4 (send_pending_invites는 50통/120초 배치 + quota abort 내장)
L '[wave4] start (leftover quota)'
docker exec auri-survey-api python /app/scripts/send_pending_invites.py *>> $log
L "[wave4] rc=$LASTEXITCODE (나머지는 5/27 정오 schtask가 dedup 이어감)"

L '=== orchestrator end ==='
