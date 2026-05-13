@echo off
setlocal EnableDelayedExpansion
REM ============================================================
REM Nightly deploy + response summary report (23:00 KST)
REM   Phase 1: git pull (scripts mount은 read-only이지만 호스트 파일 갱신만으로 충분)
REM   Phase 2: 분야별 응답 현황 텔레그램 보고
REM
REM 호출: schtasks `\auri-survey-deploy-YYYY-MM-DD` 단발 등록 (option B 패턴)
REM ============================================================

set REPO=D:\docker\auri-ai-survey
set LOGDIR=%REPO%\logs
set TS=%date:~0,4%%date:~5,2%%date:~8,2%-%time:~0,2%%time:~3,2%
set TS=%TS: =0%
set LOGFILE=%LOGDIR%\nightly-deploy-%TS%.log

cd /d "%REPO%"

if not exist "%LOGDIR%" mkdir "%LOGDIR%"

echo === [%date% %time%] nightly deploy start ===                    >> "%LOGFILE%"
echo repo=%REPO% logfile=%LOGFILE%                                    >> "%LOGFILE%"
echo.                                                                 >> "%LOGFILE%"

REM ---------- Load Telegram credentials (.telegram.env not in git) ----------
set TG_ENV=%REPO%\.telegram.env
if exist "%TG_ENV%" (
    for /f "usebackq tokens=1,2 delims==" %%A in ("%TG_ENV%") do set %%A=%%B
)

REM ---------- Phase 1: git pull ----------
echo --- [%time%] phase 1 : git pull ---                              >> "%LOGFILE%"
git pull origin master                                                >> "%LOGFILE%" 2>&1
set PULL_EXIT=!ERRORLEVEL!

REM ---------- Phase 2: 분야별 응답 현황 텔레그램 보고 ----------
echo.                                                                 >> "%LOGFILE%"
echo --- [%time%] phase 2 : response summary telegram ---             >> "%LOGFILE%"
docker exec -e TELEGRAM_BOT_TOKEN=!TELEGRAM_BOT_TOKEN! -e TELEGRAM_CHAT_ID=!TELEGRAM_CHAT_ID! auri-survey-api python /app/scripts/report_response_summary.py >> "%LOGFILE%" 2>&1
set REPORT_EXIT=!ERRORLEVEL!

echo.                                                                 >> "%LOGFILE%"
echo === [%date% %time%] done. pull=!PULL_EXIT! report=!REPORT_EXIT! === >> "%LOGFILE%"

endlocal
