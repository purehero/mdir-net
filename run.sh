#!/bin/sh
# 어느 위치에서 호출해도 MDirNet 을 실행해주는 macOS·Linux 런처

set -e

# 스크립트가 놓인 폴더를 찾아 그곳을 모듈 검색 경로로 삼는다.
# 이렇게 해두면 사용자가 cd 하지 않아도 python -m mdirnet 이 모듈을 찾는다
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)

if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo "Python 을 찾을 수 없습니다. Python 3.9 이상을 설치하세요." >&2
    exit 1
fi

# 전송 암호화에 cryptography 가 필요하다. 없으면 여기서 알려준다 -
# 모듈 안에서 ImportError 로 터지면 사용자는 무슨 일인지 알기 어렵다
if ! "$PY" -c "import cryptography" >/dev/null 2>&1; then
    echo "cryptography 가 없습니다. 다음을 실행하세요:" >&2
    echo "    $PY -m pip install -r \"$HERE/requirements.txt\"" >&2
    exit 1
fi

# "$@" 로 넘겨받은 인자를 그대로 전달한다 (--name, --root 등)
PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}" exec "$PY" -m mdirnet "$@"
