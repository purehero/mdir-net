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

# 전송 암호화에 cryptography 가 필요하다. 없으면 여기서 처리한다 -
# 모듈 안에서 ImportError 로 터지면 사용자는 무슨 일인지 알기 어렵다
if ! "$PY" -c "import cryptography" >/dev/null 2>&1; then
    REQ="$HERE/requirements.txt"

    # 사람이 보고 있을 때만 묻는다. 파이프나 스크립트 안에서 실행되면 물어봐야
    # 답할 사람이 없어 그대로 멈춘다
    if [ -t 0 ] && [ -t 1 ]; then
        echo "전송을 암호화하는 데 쓰는 cryptography 가 없습니다."
        echo "  설치할 곳: $PY"
        printf '지금 설치할까요? [y/N] '
        read -r answer
        case "$answer" in
            [Yy] | [Yy][Ee][Ss])
                echo "설치하는 중..."
                # set -e 가 걸려 있어 실패하면 그냥 죽는다. 직접 받아서 안내한다
                if "$PY" -m pip install -r "$REQ"; then
                    :
                else
                    echo "" >&2
                    echo "설치하지 못했습니다. 다음 중 하나를 해보세요:" >&2
                    echo "    $PY -m pip install --user -r \"$REQ\"" >&2
                    echo "    또는 가상환경을 만들어 그 안에서 설치" >&2
                    exit 1
                fi

                # pip 이 성공해도 다른 곳에 깔렸을 수 있다. 실제로 되는지 본다
                if ! "$PY" -c "import cryptography" >/dev/null 2>&1; then
                    echo "설치는 됐는데 이 파이썬에서 보이지 않습니다." >&2
                    echo "다른 곳에 깔린 것 같습니다: $PY" >&2
                    exit 1
                fi
                echo "설치했습니다."
                ;;
            *)
                echo "설치하지 않았습니다. 암호화 없이는 실행할 수 없습니다." >&2
                echo "나중에 직접 설치하려면:" >&2
                echo "    $PY -m pip install -r \"$REQ\"" >&2
                exit 1
                ;;
        esac
    else
        echo "cryptography 가 없습니다. 다음을 실행하세요:" >&2
        echo "    $PY -m pip install -r \"$REQ\"" >&2
        exit 1
    fi
fi

# "$@" 로 넘겨받은 인자를 그대로 전달한다 (--name, --root 등)
PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}" exec "$PY" -m mdirnet "$@"
