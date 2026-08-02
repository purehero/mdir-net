#!/usr/bin/env python3
# 화면 없는 MDirNet 노드 — 다른 구현체가 진짜 파이썬 구현과 붙는지 확인하는 도구
#
#   python3 tools/peer-probe.py --share ./어떤폴더 --accept --seconds 120
#
# `mdirnet` 의 Server / Session / Discovery 를 **그대로** 쓴다. 여기서 규격을
# 흉내내면 상호운용을 확인하는 의미가 없다. TUI 만 걷어낸 셈이다.
#
# --accept 는 들어오는 연결을 묻지 않고 받는다. 자동 검증용이므로 신뢰하는
# 네트워크에서만 쓸 것.

import argparse
import os
import queue
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mdirnet import config, discovery, session, shares as shares_mod  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="화면 없는 MDirNet 노드")
    parser.add_argument("--share", action="append", required=True, help="공유할 폴더 (여러 번)")
    parser.add_argument("--name", default="파이썬 노드")
    parser.add_argument("--accept", action="store_true", help="들어오는 연결을 자동 승인")
    parser.add_argument("--seconds", type=float, default=120.0)
    parser.add_argument("--no-discovery", action="store_true")
    args = parser.parse_args()

    shares = shares_mod.ShareSet()
    for path in args.share:
        share = shares.add(path)
        print("공유       : %s -> %s" % (share.name, share.path))

    events = queue.Queue()
    server = session.Server(args.name, sys.platform, shares, events)
    port = server.bind()
    server.start()

    node_id = os.urandom(8).hex()
    disco = None
    if not args.no_discovery:
        # **실제로 잡은 포트**를 알려야 한다. TCP_PORT 를 그대로 쓰면 아무도 못 붙는다
        disco = discovery.Discovery(node_id, args.name, port, sys.platform)
        disco.start()

    print("이름       : %s" % args.name)
    print("TCP 포트   : %d" % port)
    print("주소       : %s" % ", ".join(discovery.local_ips()))
    print("승인       : %s" % ("자동" if args.accept else "무시 (거부됨)"))
    print("-" * 60)
    sys.stdout.flush()

    sessions = []
    deadline = time.monotonic() + args.seconds
    try:
        while time.monotonic() < deadline:
            try:
                kind, payload = events.get(timeout=0.5)
            except queue.Empty:
                continue

            stamp = time.strftime("%H:%M:%S")
            if kind == "approval":
                print("%s 승인요청   %s (%s, %s)" % (stamp, payload.name, payload.host, payload.platform))
                payload.decide(args.accept)
            elif kind == "connected":
                sessions.append(payload)
                print("%s 연결됨     %s" % (stamp, payload.peer_name))
            elif kind == "received":
                print("%s 받음       %s" % (stamp, payload))
            elif kind == "remote_changed":
                print("%s 바뀜       %s" % (stamp, payload))
            elif kind == "disconnected":
                print("%s 끊김       %s" % (stamp, payload))
            elif kind == "approval_expired":
                print("%s 승인만료   %s" % (stamp, payload.name))
            else:
                print("%s %s %s" % (stamp, kind, payload))
            sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    finally:
        for live in sessions:
            try:
                live.close()
            except Exception:
                pass
        server.stop()
        if disco:
            disco.stop()

    print("-" * 60)
    print("연결 %d건" % len(sessions))
    return 0


if __name__ == "__main__":
    sys.exit(main())
