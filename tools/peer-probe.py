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
import hashlib
import os
import queue
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mdirnet import config, discovery, session, shares as shares_mod  # noqa: E402


def exercise(live, work_dir):
    """붙은 상대에게 한 바퀴 요청을 걸어본다. 상호운용 확인용."""
    print("  home      : %s" % live.remote_home())

    root_path, roots = live.remote_list(shares_mod.ROOT)
    names = [e["name"] for e in roots]
    print("  공유      : %s" % names)
    if not names:
        print("  ! 상대에게 공유가 없다")
        return

    share = names[0]
    _, entries = live.remote_list(share)
    print("  목록      : %s" % [e["name"] for e in entries][:10])

    files = [e for e in entries if not e["is_dir"]]
    if files:
        target = Path(work_dir) / ("내려받음_" + files[0]["name"])
        got = live.pull_file("%s/%s" % (share, files[0]["name"]), target)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        print("  내려받음  : %s  %d 바이트" % (files[0]["name"], got))
        print("              sha256 %s" % digest)

    # 올려보낼 것을 만들어 보낸다
    source = Path(work_dir) / "맥이보낸것.bin"
    payload = hashlib.sha256(b"macpush").digest() * 20000  # 640000 바이트
    source.write_bytes(payload)
    sent = live.push_file(source, "%s/맥이보낸것.bin" % share)
    print("  올려보냄  : %d 바이트" % sent)
    print("              sha256 %s" % hashlib.sha256(payload).hexdigest())

    live.remote_mkdir("%s/맥이만든폴더" % share)
    live.remote_rename("%s/맥이만든폴더" % share, "이름바뀜")
    live.remote_delete("%s/이름바뀜" % share)
    print("  폴더      : 만들기/이름바꾸기/지우기 확인")


def main():
    parser = argparse.ArgumentParser(description="화면 없는 MDirNet 노드")
    parser.add_argument("--share", action="append", required=True, help="공유할 폴더 (여러 번)")
    parser.add_argument("--name", default="파이썬 노드")
    parser.add_argument("--accept", action="store_true", help="들어오는 연결을 자동 승인")
    parser.add_argument("--seconds", type=float, default=120.0)
    parser.add_argument("--no-discovery", action="store_true")
    parser.add_argument(
        "--connect-to",
        help="이 이름의 상대를 찾으면 내가 먼저 붙어 한 바퀴 요청을 걸어본다",
    )
    parser.add_argument("--work", default=".", help="--connect-to 로 받은 것을 둘 폴더")
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
    print("이 기기 지문: %s" % server.fingerprint)
    print("주소       : %s" % ", ".join(discovery.local_ips()))
    print("승인       : %s" % ("자동" if args.accept else "무시 (거부됨)"))
    print("-" * 60)
    sys.stdout.flush()

    sessions = []
    dialed = False
    deadline = time.monotonic() + args.seconds
    try:
        while time.monotonic() < deadline:
            # 내가 먼저 붙어야 하는 경우. 상대를 찾을 때까지 기다린다
            if args.connect_to and not dialed and disco:
                match = next(
                    (p for p in disco.peers() if args.connect_to in p.name), None
                )
                if match:
                    dialed = True
                    print("찾음       : %s" % match.label)
                    sys.stdout.flush()
                    try:
                        live = session.connect(match, args.name, sys.platform, shares, events)
                        sessions.append(live)
                        print("붙었다     : %s" % live.peer_name)
                        print("  상대 지문 : %s" % live.peer_fingerprint)
                        exercise(live, args.work)
                    except Exception as exc:
                        print("붙지 못함  : %s: %s" % (type(exc).__name__, exc))
                    sys.stdout.flush()

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
