#!/bin/sh
# Finder 에서 더블클릭하면 Terminal 창에서 MDirNet 을 띄우는 런처

# Finder 로 실행하면 작업 디렉터리가 홈으로 잡히므로, 자기 위치를 직접 찾아
# 같은 폴더의 run.sh 에 위임한다
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
exec "$HERE/run.sh" "$@"
