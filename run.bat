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
REM 전송 암호화에 cryptography 가 필요하다. 없으면 여기서 알려준다 -
REM 모듈 안에서 ImportError 로 터지면 사용자는 무슨 일인지 알기 어렵다
%PY% -c "import cryptography" >nul 2>&1
if errorlevel 1 (
    echo cryptography 가 없습니다. 다음을 실행하세요: 1>&2
    echo     %PY% -m pip install -r "%HERE%requirements.txt" 1>&2
    exit /b 1
)

%PY% -m mdirnet %*
goto :done

:done
exit /b %errorlevel%
