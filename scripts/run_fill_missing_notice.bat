@echo off
setlocal
set LOGDIR=D:\docker\auri-ai-survey\logs
set TS=%date:~0,4%%date:~5,2%%date:~8,2%-%time:~0,2%%time:~3,2%
set TS=%TS: =0%
set LOGFILE=%LOGDIR%\fill-missing-notice-%TS%.log

if not exist "%LOGDIR%" mkdir "%LOGDIR%"

echo === fill-missing-notice dispatch start %DATE% %TIME% === > "%LOGFILE%"

docker exec auri-survey-api python /app/scripts/send_fill_missing_notice.py >> "%LOGFILE%" 2>&1
set RC=%ERRORLEVEL%
echo === exit code %RC% at %DATE% %TIME% === >> "%LOGFILE%"

REM Telegram phase report (재사용)
docker exec auri-survey-api python /app/scripts/report_dispatch_telegram.py >> "%LOGFILE%" 2>&1

exit /b %RC%
