#!/bin/sh
# MDirNet 이 들어오는 연결을 받을 수 있게 macOS 방화벽에 허용하는 스크립트 (sudo 필요)
#
#   sh tools/allow-firewall.sh          허용
#   sh tools/allow-firewall.sh --remove 되돌리기
#
# macOS 방화벽은 포트가 아니라 **실행 파일** 단위로 허용한다. 그래서 이 앱을
# 돌리는 python 실행 파일을 목록에 넣는다. 파이썬을 다른 것으로 바꿔 실행하면
# 그 실행 파일도 따로 넣어야 한다.

set -e

FW=/usr/libexec/ApplicationFirewall/socketfilterfw
if [ ! -x "$FW" ]; then
    echo "macOS 방화벽 도구를 찾을 수 없습니다. 이 스크립트는 macOS 전용입니다." >&2
    echo "Linux 라면 배포판 방화벽에서 TCP 9600-9609, UDP 9601 을 열어주세요." >&2
    exit 1
fi

HERE=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)

if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo "Python 을 찾을 수 없습니다. Python 3.9 이상을 설치하세요." >&2
    exit 1
fi

# which 가 아니라 sys.executable 을 쓴다. 심링크나 셸 함수를 거치면 실제로
# 실행되는 파일과 달라지고, 방화벽은 실제 파일 경로로 판정한다
PY_BIN=$($PY -c 'import sys; print(sys.executable)')
PORTS=$(PYTHONPATH="$HERE" $PY -c 'from mdirnet import config; print(config.TCP_PORT, config.UDP_PORT)')

if [ "$1" = "--remove" ]; then
    sudo "$FW" --remove "$PY_BIN" >/dev/null
    echo "목록에서 제거했습니다: $PY_BIN"
    exit 0
fi

sudo "$FW" --add "$PY_BIN" >/dev/null
sudo "$FW" --unblockapp "$PY_BIN" >/dev/null

echo "허용했습니다: $PY_BIN"
echo "쓰는 포트: TCP $(echo "$PORTS" | cut -d' ' -f1) 이후 10개, UDP $(echo "$PORTS" | cut -d' ' -f2)"
echo ""
"$FW" --getglobalstate
echo "방화벽이 꺼져 있으면 애초에 막는 것이 없으므로 지금 당장 달라지는 것은 없습니다."
