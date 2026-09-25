@echo off
setlocal enabledelayedexpansion
REM Builds and installs pgvector into an installed PostgreSQL.
REM Run from "x64 Native Tools Command Prompt for VS 2022" as Administrator.
REM
REM Usage: install_pgvector.bat [PostgreSQL folder] [pgvector git tag]
REM   PostgreSQL folder  default: highest-numbered folder under C:\Program Files\PostgreSQL
REM   pgvector git tag   default: v0.8.1

REM check the pgvector GitHub releases page; PostgreSQL 18 needs a release that supports it
set "PGVECTOR_TAG=v0.8.1"
if not "%~2"=="" set "PGVECTOR_TAG=%~2"

if not "%~1"=="" (
    set "PGROOT_RAW=%~1"
) else (
    set "BEST=0"
    for /d %%D in ("C:\Program Files\PostgreSQL\*") do (
        if %%~nxD gtr !BEST! set "BEST=%%~nxD"
    )
    if "!BEST!"=="0" (
        echo No PostgreSQL folder found under C:\Program Files\PostgreSQL.
        goto fail
    )
    set "PGROOT_RAW=C:\Program Files\PostgreSQL\!BEST!"
)

REM Convert the path to an 8.3 short path to eliminate spaces for nmake
for %%I in ("!PGROOT_RAW!") do set "PGROOT=%%~sI"

echo PGROOT=%PGROOT%
echo pgvector tag=%PGVECTOR_TAG%

cd /d "%TEMP%"
if exist pgvector rmdir /s /q pgvector

git clone --branch %PGVECTOR_TAG% https://github.com/pgvector/pgvector.git
if errorlevel 1 goto fail

cd pgvector

nmake /F Makefile.win
if errorlevel 1 goto fail

nmake /F Makefile.win install
if errorlevel 1 goto fail

echo.
echo SUCCESS: pgvector %PGVECTOR_TAG% installed.
exit /b 0

:fail
echo.
echo FAILED: pgvector build/install did not complete. See the errors above.
exit /b 1