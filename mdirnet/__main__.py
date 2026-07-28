# 명령행 인자를 읽어 MDirNet UI 를 띄우는 진입점

import argparse
import platform
import socket
import sys
from pathlib import Path

from . import config, shares as shares_mod
from .ui.app import App


def default_name():
    """호스트 이름에서 화면에 쓸 짧은 이름을 뽑는다."""
    name = socket.gethostname().split(".")[0]
    return name or "unknown"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="mdirnet",
        description="같은 공유기에 연결된 PC 끼리 파일을 주고받는 MDir 풍 도구",
    )
    parser.add_argument("--name", default=default_name(), help="상대에게 보여줄 이름")
    parser.add_argument(
        "--share",
        action="append",
        metavar="PATH",
        default=[],
        help="상대에게 열어줄 폴더. 여러 번 쓸 수 있다 (이번 실행에만 적용, 저장 안 함)",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="--share 하나와 같다 (기존 호환용)",
    )
    parser.add_argument("--port", type=int, default=config.TCP_PORT, help="세션 TCP 포트")
    parser.add_argument("--udp-port", type=int, default=config.UDP_PORT, help="발견용 UDP 포트")
    parser.add_argument(
        "--no-discovery",
        action="store_true",
        help="자동 발견을 끈다 (브로드캐스트가 막힌 망에서)",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    if not sys.stdin.isatty():
        print("터미널에서 실행해야 합니다.", file=sys.stderr)
        return 1

    # 인자로 폴더를 줬으면 그것만 쓰고 저장하지 않는다. 저장된 목록을 건드리지
    # 않으므로 시험용이나 일회성 실행에 안전하다
    given = list(args.share) + ([args.root] if args.root else [])
    shares = shares_mod.ShareSet()
    persist = not given

    if given:
        for raw in given:
            path = Path(raw).expanduser()
            if not path.is_dir():
                print("공유할 폴더가 없습니다: %s" % path, file=sys.stderr)
                return 1
            shares.add(path)
    else:
        shares.load()

    app = App(
        my_name=args.name,
        shares=shares,
        persist=persist,
        tcp_port=args.port,
        udp_port=args.udp_port,
        platform_name=platform.system().lower() or sys.platform,
        use_discovery=not args.no_discovery,
    )

    try:
        app.run()
    except KeyboardInterrupt:
        app.shutdown()

    print("%s 를 종료했습니다." % config.APP_NAME)
    return 0


if __name__ == "__main__":
    sys.exit(main())
