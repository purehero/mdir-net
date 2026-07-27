# 명령행 인자를 읽어 MDirNet UI 를 띄우는 진입점

import argparse
import platform
import socket
import sys
from pathlib import Path

from . import config
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
        "--root",
        default=None,
        help="상대에게 열어줄 공유 폴더 (생략하면 실행 후 선택상자로 고른다)",
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

    # --root 를 생략하면 None 을 그대로 넘긴다. App 이 실행 직후 선택상자를 띄운다
    root = None
    if args.root is not None:
        root = Path(args.root).expanduser()
        if not root.is_dir():
            print("공유 폴더가 없습니다: %s" % root, file=sys.stderr)
            return 1

    if not sys.stdin.isatty():
        print("터미널에서 실행해야 합니다.", file=sys.stderr)
        return 1

    app = App(
        my_name=args.name,
        root=root,
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
