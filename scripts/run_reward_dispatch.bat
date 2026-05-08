@echo off
setlocal EnableDelayedExpansion
REM ============================================================
REM Reward dispatch -- schtasks scheduled
REM   Phase 0: telegram start notice
REM   Phase 1: reward_notice  (responders without reward consent)
REM   Phase 2: reward_resend  (invite recipients without response)
REM   Phase 3: telegram report (reward)
REM   Phase 4: pending invites (wave2 + wave3)   -- only when RESEND_EXIT=0
REM   Phase 5: telegram report (invites)
REM Each phase script self-aborts on quota; next run dedupes via DB.
REM Flat if-goto layout (no parens block) -- prior version with
REM "if () else ()" + EnableDelayedExpansion + redirection broke
REM cmd parser at Phase 4 entry under schtasks context.
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

REM ---------- Load Telegram credentials (.telegram.env not in git) ----------
set TG_ENV=%REPO%\.telegram.env
if exist "%TG_ENV%" (
    for /f "usebackq tokens=1,2 delims==" %%A in ("%TG_ENV%") do set %%A=%%B
)

REM scripts/ is mounted into the container read-only via docker-compose
REM volume (./scripts:/app/scripts:ro), so host-side edits are seen by
REM the running container without rebuild.

REM ---------- Phase 0: Telegram start notice ----------
echo --- [%time%] phase 0 : telegram start ---                       >> "%LOGFILE%"
docker exec -e TELEGRAM_BOT_TOKEN=!TELEGRAM_BOT_TOKEN! -e TELEGRAM_CHAT_ID=!TELEGRAM_CHAT_ID! auri-survey-api python /app/scripts/notify_telegram.py >> "%LOGFILE%" 2>&1
set NOTIFY_EXIT=!ERRORLEVEL!

REM ---------- Phase 1: reward_notice ----------
echo --- [%time%] phase 1 : reward_notice ---                         >> "%LOGFILE%"
docker exec auri-survey-api python /app/scripts/send_reward_notice.py >> "%LOGFILE%" 2>&1
set NOTICE_EXIT=!ERRORLEVEL!

REM ---------- Phase 2: reward_resend ----------
echo.                                                                 >> "%LOGFILE%"
echo --- [%time%] phase 2 : reward_resend ---                         >> "%LOGFILE%"
docker exec auri-survey-api python /app/scripts/send_reward_resend.py >> "%LOGFILE%" 2>&1
set RESEND_EXIT=!ERRORLEVEL!

REM ---------- Phase 3: Telegram reward report ----------
echo.                                                                 >> "%LOGFILE%"
echo --- [%time%] phase 3 : reward telegram report ---                >> "%LOGFILE%"
docker exec -e TELEGRAM_BOT_TOKEN=!TELEGRAM_BOT_TOKEN! -e TELEGRAM_CHAT_ID=!TELEGRAM_CHAT_ID! auri-survey-api python /app/scripts/report_dispatch_telegram.py >> "%LOGFILE%" 2>&1
set REPORT_EXIT=!ERRORLEVEL!

set INVITE_EXIT=skip
set INVITE_REPORT_EXIT=skip

REM ---------- Phase 4/5: only enter when resend cleanly succeeded ----------
if not "!RESEND_EXIT!"=="0" goto :PHASE45_SKIP

echo.                                                                 >> "%LOGFILE%"
echo --- [%time%] phase 4 : pending invites (wave2 + wave3) ---       >> "%LOGFILE%"
docker exec auri-survey-api python /app/scripts/send_pending_invites.py >> "%LOGFILE%" 2>&1
set INVITE_EXIT=!ERRORLEVEL!

echo.                                                                 >> "%LOGFILE%"
echo --- [%time%] phase 5 : invite telegram report ---                >> "%LOGFILE%"
docker exec -e TELEGRAM_BOT_TOKEN=!TELEGRAM_BOT_TOKEN! -e TELEGRAM_CHAT_ID=!TELEGRAM_CHAT_ID! auri-survey-api python /app/scripts/report_invites_telegram.py >> "%LOGFILE%" 2>&1
set INVITE_REPORT_EXIT=!ERRORLEVEL!

goto :PHASE45_DONE

:PHASE45_SKIP
echo.                                                                 >> "%LOGFILE%"
echo --- phase 4/5 skipped : resend abort/error (resend_exit=!RESEND_EXIT!) --- >> "%LOGFILE%"

:PHASE45_DONE
echo.                                                                 >> "%LOGFILE%"
echo === [%date% %time%] done. notify=!NOTIFY_EXIT! notice=!NOTICE_EXIT! resend=!RESEND_EXIT! report=!REPORT_EXIT! invite=!INVITE_EXIT! invite_report=!INVITE_REPORT_EXIT! === >> "%LOGFILE%"
echo (exit codes: 0=ok, 2=quota_aborted_resume_next_day, other=error) >> "%LOGFILE%"

endlocal
