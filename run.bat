@echo off
REM 어느 위치에서 호출해도 MDirNet 을 실행해주는 Windows 런처

setlocal

REM %~dp0 는 이 배치 파일이 놓인 폴더다 (끝에 역슬래시가 붙는다).
REM 그곳을 모듈 검색 경로로 삼아 cd 없이도 mdirnet 을 찾게 한다
set "HERE=%~dp0"
if "%PYTHONPATH%"=="" (
    set "PYTHONPATH=%HERE%"
) else (
    set "PYTHONPATH=%HERE%;%PYTHONPATH%"
)

REM py 런처가 있으면 그것을 쓴다. 여러 버전이 깔린 환경에서 3.x 를 확실히 고른다
py -3 -c "" >nul 2>&1
if not errorlevel 1 (
    set "PY=py -3"
    goto :found
)

python -c "" >nul 2>&1
if not errorlevel 1 (
    set "PY=python"
    goto :found
)

echo Python 을 찾을 수 없습니다. Python 3.9 이상을 설치하세요. 1>&2
exit /b 1

:found
REM 전송 암호화에 cryptography 가 필요하다. 없으면 여기서 처리한다 -
REM 모듈 안에서 ImportError 로 터지면 사용자는 무슨 일인지 알기 어렵다
%PY% -c "import cryptography" >nul 2>&1
if not errorlevel 1 goto :ready

echo 전송을 암호화하는 데 쓰는 cryptography 가 없습니다.
echo   설치할 곳: %PY%
set /p ANSWER="지금 설치할까요? [y/N] "
if /i "%ANSWER%"=="y" goto :install
if /i "%ANSWER%"=="yes" goto :install

echo 설치하지 않았습니다. 암호화 없이는 실행할 수 없습니다. 1>&2
echo 나중에 직접 설치하려면: 1>&2
echo     %PY% -m pip install -r "%HERE%requirements.txt" 1>&2
exit /b 1

:install
echo 설치하는 중...
%PY% -m pip install -r "%HERE%requirements.txt"
if errorlevel 1 (
    echo. 1>&2
    echo 설치하지 못했습니다. 다음을 해보세요: 1>&2
    echo     %PY% -m pip install --user -r "%HERE%requirements.txt" 1>&2
    exit /b 1
)

REM pip 이 성공해도 다른 곳에 깔렸을 수 있다. 실제로 되는지 본다
%PY% -c "import cryptography" >nul 2>&1
if errorlevel 1 (
    echo 설치는 됐는데 이 파이썬에서 보이지 않습니다: %PY% 1>&2
    exit /b 1
)
echo 설치했습니다.

:ready

%PY% -m mdirnet %*
goto :done

:done
exit /b %errorlevel%
