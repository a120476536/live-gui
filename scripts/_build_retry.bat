@echo off
REM ============================================================
REM Generic retry wrapper.
REM   usage: set "BUILD_MAX_TRY=3"
REM          call _build_retry.bat <full command line>
REM   Purpose: some antivirus products temporarily lock freshly copied
REM            DLLs and make the command fail with errno 13. Retry helps.
REM   This file is intentionally pure ASCII, CRLF, no BOM.
REM ============================================================
setlocal enabledelayedexpansion
if not defined BUILD_MAX_TRY set "BUILD_MAX_TRY=3"
set /a TRY=0
:retry
set /a TRY+=1
echo [retry] attempt !TRY!/%BUILD_MAX_TRY%: %~1
%*
if not errorlevel 1 (
  echo [retry] succeeded on attempt !TRY!
  endlocal
  exit /b 0
)
set "LAST=!errorlevel!"
if !TRY! lss %BUILD_MAX_TRY% (
  echo [retry] failed with code !LAST!, waiting 3s then retrying...
  ping -n 4 127.0.0.1 >nul
  goto :retry
)
echo [retry] gave up after !TRY! attempts, last code !LAST!
endlocal
exit /b 1
