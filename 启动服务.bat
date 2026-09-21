@echo off
chcp 65001 >nul

rem ===========================================================================
rem  DrawClimateChartWeb - Windows one-click launcher (double-click this file)
rem
rem  Flow : locate Python -> check version -> auto-install missing dependencies
rem         -> start the local server -> open the page in the default browser
rem  Stop : close this console window, or press Ctrl+C
rem         (answer Y if it asks "Terminate batch job (Y/N)?")
rem
rem  Extra arguments are forwarded to run.py, for example:
rem      %~nx0 --port 9000        use another port (default 8765)
rem      %~nx0 --no-browser       do not open the browser automatically
rem      %~nx0 --host 0.0.0.0     allow other machines on the LAN
rem
rem  NOTE: keep this file ASCII-only before/around chcp. cmd.exe reads a batch
rem        file with the *current* code page, so mixing code pages makes it
rem        mis-parse lines. Chinese text is only used in messages printed after
rem        chcp 65001 takes effect.
rem ===========================================================================

setlocal
cd /d "%~dp0"
title WMO 城市气候统计图 · 本地服务

rem UTF-8 everywhere: console (chcp above) and Python output
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "PORT=8765"
rem if "--port N" was given explicitly, probe that port too
if "%~1"=="--port" if not "%~2"=="" set "PORT=%~2"
set "URL=http://127.0.0.1:%PORT%/"

echo.
echo   WMO 城市气候统计图 · 网页工具
echo   ==========================================================

rem ---- 1/4 locate Python: project venv > py launcher > python on PATH -----
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY (
  where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo   [错误] 没有找到 Python，请先安装 Python 3.9 或更高版本：
  echo          https://www.python.org/downloads/
  echo          安装时请勾选 Add python.exe to PATH，装好后重新双击本文件。
  echo.
  pause
  exit /b 1
)
echo   使用解释器：
%PY% -V

rem ---- 2/4 version check (needs 3.9+, 3.10+ recommended) -------------------
%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
if errorlevel 1 (
  echo   [错误] 当前 Python 版本过低，需要 3.9 或更高版本。
  echo.
  pause
  exit /b 1
)

rem ---- 3/4 dependencies: install when missing, retry with a CN mirror -------
%PY% -c "import matplotlib, yaml, openpyxl" >nul 2>nul
if errorlevel 1 (
  echo   首次运行：正在安装依赖 matplotlib / openpyxl / PyYAML ...
  %PY% -m pip install -r requirements.txt
  if errorlevel 1 (
    echo   默认源安装失败，改用清华镜像重试 ...
    %PY% -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
  )
  %PY% -c "import matplotlib, yaml, openpyxl" >nul 2>nul
  if errorlevel 1 (
    echo   [错误] 依赖安装失败，请手动执行： %PY% -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
  )
)

rem ---- 4/4 already running? then just open the page, no second instance ----
rem raw TCP probe (bypasses the system proxy); the index page must carry the
rem stylesheet link, which is what identifies our own server.
powershell -NoProfile -Command "try{$c=New-Object Net.Sockets.TcpClient;$c.ReceiveTimeout=1500;$c.SendTimeout=1500;$c.Connect('127.0.0.1',%PORT%);$s=$c.GetStream();$req='GET / HTTP/1.0'+[char]13+[char]10+'Host: 127.0.0.1'+[char]13+[char]10+[char]13+[char]10;$b=[Text.Encoding]::ASCII.GetBytes($req);$s.Write($b,0,$b.Length);$buf=New-Object byte[] 8192;$acc='';$end=(Get-Date).AddSeconds(2);while((Get-Date) -lt $end){if($s.DataAvailable){$n=$s.Read($buf,0,$buf.Length);if($n -le 0){break};$acc+=[Text.Encoding]::ASCII.GetString($buf,0,$n);if($acc.Contains('/static/css/style.css')){break}}else{Start-Sleep -Milliseconds 40}};$c.Close();if($acc.Contains('/static/css/style.css')){exit 0};exit 1}catch{exit 1}"
if not errorlevel 1 (
  echo   本工具已在 %URL% 运行，直接打开网页（不再启动第二个实例）。
  start "" "%URL%"
  timeout /t 3 >nul
  exit /b 0
)

echo   正在启动服务，就绪后会自动打开网页 ...
echo   停止服务：关闭本窗口，或按 Ctrl+C（若问「终止批处理操作吗」，按 Y）。
echo   ==========================================================
echo.
%PY% run.py %*
echo.
echo   服务已退出，窗口不会自动关闭。
pause
