@echo off
setlocal EnableDelayedExpansion
REM ============================================================
REM 사례품 안내 일괄 발송 — schtasks 스케줄링 대상
REM Phase 1: reward_notice  (응답자 미동의 ~14)
REM Phase 2: reward_resend  (옛 invite 수신·미응답자 ~498)
REM 한도 초과 시 각 스크립트가 자체 abort + 다음 실행 시 자동 dedup
REM ============================================================

set REPO=D:\docker\auri-ai-survey
set LOGDIR=%REPO%\logs
set TS=%date:~0,4%%date:~5,2%%date:~8,2%-%time:~0,2%%time:~3,2%
set TS=%TS: =0%
set LOGFILE=%LOGDIR%\reward-dispatch-%TS%.log

cd /d "%REPO%"

if not exist "%LOGDIR%" mkdir "%LOGDIR%"

echo === [%date% %time%] reward dispatch start ===                  >> "%LOGFILE%"
echo repo=%REPO% logfile=%LOGFILE%                                    >> "%LOGFILE%"
echo.                                                                 >> "%LOGFILE%"

REM ---------- 텔레그램 자격증명 로드 (.telegram.env 는 git 미포함) ----------
set TG_ENV=%REPO%\.telegram.env
if exist "%TG_ENV%" (
    for /f "usebackq tokens=1,2 delims==" %%A in ("%TG_ENV%") do set %%A=%%B
)

REM scripts/는 docker-compose.yml의 volume mount(./scripts:/app/scripts:ro)로 컨테이너에
REM read-only 노출됨. 매번 inject 안 하고 호스트 파일을 직접 호출 — 컨테이너 재생성에도 영향 없음.
REM (이전 inject 방식은 schtasks 비로그인 컨텍스트에서 ^| escape 깨짐 + tmpfs /tmp wipe로 실패한 이력 있음.)

REM ---------- Phase 1: reward_notice (응답자 미동의) ----------
echo --- [%time%] phase 1 : reward_notice ---                         >> "%LOGFILE%"
docker exec auri-survey-api python /app/scripts/send_reward_notice.py >> "%LOGFILE%" 2>&1
set NOTICE_EXIT=!ERRORLEVEL!

REM ---------- Phase 2: reward_resend (미응답자 재안내) ----------
echo.                                                                 >> "%LOGFILE%"
echo --- [%time%] phase 2 : reward_resend ---                         >> "%LOGFILE%"
docker exec auri-survey-api python /app/scripts/send_reward_resend.py >> "%LOGFILE%" 2>&1
set RESEND_EXIT=!ERRORLEVEL!

REM ---------- Phase 3: Telegram 결과 보고 ----------
echo.                                                                 >> "%LOGFILE%"
echo --- [%time%] phase 3 : telegram report ---                       >> "%LOGFILE%"
docker exec -e TELEGRAM_BOT_TOKEN=!TELEGRAM_BOT_TOKEN! -e TELEGRAM_CHAT_ID=!TELEGRAM_CHAT_ID! auri-survey-api python /app/scripts/report_dispatch_telegram.py >> "%LOGFILE%" 2>&1
set REPORT_EXIT=!ERRORLEVEL!

echo.                                                                 >> "%LOGFILE%"
echo === [%date% %time%] done. notice_exit=!NOTICE_EXIT! resend_exit=!RESEND_EXIT! report_exit=!REPORT_EXIT! === >> "%LOGFILE%"
echo (exit codes: 0=ok, 2=quota_aborted_resume_next_day, other=error) >> "%LOGFILE%"

endlocal
