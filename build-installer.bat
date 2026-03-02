@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo [1/4] Building Python backend (PyInstaller)...
py -m pip install --disable-pip-version-check -q pyinstaller
if errorlevel 1 goto :fail
py -m PyInstaller --noconfirm VoiceKey.spec
if errorlevel 1 goto :fail

echo [2/4] Building Tauri overlay (release)...
pushd overlay-ui
call pnpm install --frozen-lockfile
if errorlevel 1 goto :popd_fail
call pnpm build
if errorlevel 1 goto :popd_fail
popd

if not exist "dist\VoiceKey\VoiceKey.exe" goto :missing_backend
if not exist "overlay-ui\src-tauri\target\release\voicekey-overlay.exe" goto :missing_overlay

echo [3/4] Locating Inno Setup compiler...
set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" goto :missing_iscc

echo [4/4] Building single-file installer...
if not exist "dist-installer" mkdir "dist-installer" >nul 2>nul
"%ISCC%" "installer\VoiceKey.iss"
if errorlevel 1 goto :fail

echo.
echo Installer created:
echo   dist-installer\VoiceKey-Setup.exe
exit /b 0

:missing_backend
echo [ERROR] Missing backend executable: dist\VoiceKey\VoiceKey.exe
goto :fail

:missing_overlay
echo [ERROR] Missing overlay executable: overlay-ui\src-tauri\target\release\voicekey-overlay.exe
goto :fail

:missing_iscc
echo [ERROR] Inno Setup compiler (ISCC.exe) not found.
echo         Expected path: "%ISCC%"
echo         Install with: winget install --id JRSoftware.InnoSetup -e
goto :fail

:popd_fail
popd

:fail
echo.
echo Build failed.
exit /b 1
