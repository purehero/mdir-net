#!/usr/bin/env python3
# 진짜 LAN 에서 탐색이 오가는지 눈으로 확인하는 도구
#
#   python3 tools/lan-probe.py --listen            누가 알리는지 지켜본다
#   python3 tools/lan-probe.py --announce          내가 알린다 (상대가 나를 보는지)
#
# 파이썬 구현의 discovery 모듈을 그대로 쓴다. 다른 구현체(안드로이드)가 이것과
# 서로를 찾아내는지 확인하는 것이 목적이라, 여기서 규격을 흉내내면 의미가 없다.

import argparse
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mdirnet import config, discovery  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="MDirNet LAN 탐색 확인")
    parser.add_argument("--listen", action="store_true", help="알림을 지켜본다")
    parser.add_argument("--announce", action="store_true", help="내 존재를 알린다")
    parser.add_argument("--name", default="파이썬 탐침")
    parser.add_argument("--port", type=int, default=config.TCP_PORT, help="알릴 TCP 포트")
    parser.add_argument("--seconds", type=float, default=20.0)
    args = parser.parse_args()

    if not (args.listen or args.announce):
        parser.error("--listen 또는 --announce 중 하나는 있어야 합니다")

    node_id = os.urandom(8).hex()
    disco = discovery.Discovery(
        node_id=node_id,
        name=args.name,
        tcp_port=args.port,
        platform=sys.platform,
    )

    print("내 id      : %s" % node_id)
    print("내 주소    : %s" % ", ".join(discovery.local_ips()))
    print("UDP 포트   : %d" % config.UDP_PORT)
    print("하는 일    : %s" % (" + ".join(
        (["알린다"] if args.announce else []) + (["듣는다"] if args.listen else [])
    )))
    print("-" * 60)

    # Discovery.start 는 둘을 함께 띄운다. 한쪽만 원하면 해당 루프만 돌린다
    stop = disco._stop
    threads = []
    if args.announce:
        threads.append(threading.Thread(target=disco._announce_loop, daemon=True))
    if args.listen:
        threads.append(threading.Thread(target=disco._listen_loop, daemon=True))
    for t in threads:
        t.start()

    seen = {}
    deadline = time.monotonic() + args.seconds
    try:
        while time.monotonic() < deadline:
            for peer in disco.peers():
                mark = "" if peer.node_id in seen else "  <- 처음 봤다"
                if peer.node_id not in seen or seen[peer.node_id] != peer.label:
                    print("%-6s %-24s %s:%-6d %-8s%s" % (
                        time.strftime("%H:%M:%S"),
                        peer.name, peer.host, peer.port, peer.platform, mark))
                    seen[peer.node_id] = peer.label
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()

    print("-" * 60)
    if seen:
        print("찾은 노드 %d개" % len(seen))
    elif args.listen:
        print("아무도 못 찾았다. 상대가 알리고 있는지, 같은 공유기인지, 방화벽을 확인하라")
    else:
        print("알리기만 했다. 상대 쪽 목록을 확인하라")
    return 0 if (seen or not args.listen) else 1


if __name__ == "__main__":
    sys.exit(main())
