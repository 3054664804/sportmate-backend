@echo off
chcp 65001 >nul
title Push sportmate-backend to GitHub
cd /d "%~dp0"

echo ============================================================
echo   Push sportmate-backend  --^>  GitHub
echo   Account : 3054664804
echo   Repo    : https://github.com/3054664804/sportmate-backend
echo ============================================================
echo.
echo   STEP 1 (one time only)
echo   ----------------------
echo   If the repo does not exist yet, open:
echo       https://github.com/new
echo   Repository name : sportmate-backend
echo   Visibility      : Public (or Private, your choice)
echo   Do NOT tick "Add a README file" / ".gitignore" / "license"
echo   ^(otherwise the first push is rejected and you must pull first^)
echo.
echo   Already created it before? Just continue.
echo.
pause

echo.
echo ------------------------------------------------------------
echo   Pushing... a browser window may pop up asking you to
echo   authorize GitHub access. Click "Authorize" and come back.
echo ------------------------------------------------------------
echo.

git push -u origin main
if errorlevel 1 goto failed

echo.
echo ============================================================
echo   DONE - your repo is live at:
echo   https://github.com/3054664804/sportmate-backend
echo ============================================================
pause
exit /b 0

:failed
echo.
echo ============================================================
echo   PUSH FAILED - find your error below
echo ============================================================
echo.
echo   "Repository not found"
echo       The repo is not created yet. Do STEP 1 above.
echo.
echo   "rejected ... (fetch first)" / "tip of your current branch is behind"
echo       You ticked "Add a README file" when creating the repo.
echo       Fix with:
echo           git pull --rebase origin main
echo           git push -u origin main
echo.
echo   "could not read Username" / "Authentication failed"
echo       The GitHub login window was closed, or the token expired.
echo       Run this script again and complete the browser login.
echo.
echo   "schannel: AcquireCredentialsHandle failed"
echo       TLS/certificate problem. Try:
echo           git config --global http.sslBackend openssl
echo       then run this script again.
echo.
pause
exit /b 0
