#!/usr/bin/env python3
# 파이썬 구현이 만드는 바이트를 뽑아 안드로이드 테스트의 골든 벡터로 박아둔다
#
#   python3 tools/gen-golden.py
#
# 두 가지를 내보낸다.
#   frames.bin / frames.json      세션 프레임 (PROTOCOL.md 3절)
#   announce.bin / announce.json  탐색 데이터그램 (PROTOCOL.md 2절)
#   fingerprints.json             연결 지문 (PROTOCOL.md 2.5절)
#
# 프레임은 하나의 파일에 **이어붙여** 내보낸다. 프레임 경계를 넘는 버퍼 처리가
# 프레이밍 버그가 사는 곳이라, 한 개씩 따로 두면 정작 중요한 것을 못 잡는다.

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mdirnet import config, fingerprint as fp_mod, protocol, shares  # noqa: E402

OUT_DIR = ROOT / "android" / "app" / "src" / "test" / "resources" / "golden"


def filler(n, seed):
    """되풀이 가능한 바이트. os.urandom 을 쓰면 매번 벡터가 바뀐다."""
    out = bytearray()
    block = seed.encode("utf-8")
    while len(out) < n:
        block = hashlib.sha256(block).digest()
        out.extend(block)
    return bytes(out[:n])


# (설명, 헤더, blob)
CASES = [
    (
        "핸드셰이크. 순수 ASCII, blob 없음",
        {"t": "hello", "v": config.PROTOCOL_VERSION, "name": "맥북", "platform": "darwin"},
        None,
    ),
    (
        "한글과 공백이 든 경로. q 가 붙은 요청",
        {"t": "list", "id": 1, "q": 1, "path": "내 문서/사진 모음"},
        None,
    ),
    (
        "목록 응답. bool 과 실수 mtime 이 섞인 배열",
        {
            "t": "list.ok",
            "id": 1,
            "path": "내 문서/사진 모음",
            "entries": [
                {"name": "2024년", "is_dir": True, "size": 0, "mtime": 1722000000.5},
                {"name": "a b c.txt", "is_dir": False, "size": 15210, "mtime": 1722000123.25},
                {"name": "따옴표\"와\\역슬래시", "is_dir": False, "size": 1, "mtime": 0.0},
            ],
        },
        None,
    ),
    (
        "뷰어. 파일 전체 크기와 앞부분 blob",
        {"t": "head.ok", "id": 2, "size": 1048576},
        filler(1024, "head"),
    ),
    (
        "전송의 보통 경우. CHUNK_SIZE 짜리 blob",
        {"t": "data", "id": 3},
        filler(config.CHUNK_SIZE, "chunk"),
    ),
    (
        "1바이트 blob. 경계값",
        {"t": "data", "id": 3},
        b"\x00",
    ),
    (
        "빈 blob. 헤더에 blob:0 이 들어가고 뒤에 바이트는 없다",
        {"t": "data", "id": 3},
        b"",
    ),
    (
        "전송 끝",
        {"t": "data.end", "id": 3},
        None,
    ),
    (
        "재귀 복사용 트리. 빈 배열도 섞는다",
        {
            "t": "tree.ok",
            "id": 4,
            "files": [{"path": "2024년/사진.jpg", "size": 102400}],
            "dirs": ["2024년", "2024년/원본"],
        },
        None,
    ),
    (
        "오류 회신. 한글 메시지",
        {"t": "err", "id": 5, "msg": "공유 목록에 없습니다: 없는것"},
        None,
    ),
    (
        "종료 인사",
        {"t": "bye"},
        None,
    ),
]


def write_announce():
    """탐색 데이터그램. 프레임 헤더와 JSON 인코딩 규칙이 다르다는 것이 요점이다.

    `discovery.py` 는 기본 옵션 `json.dumps` 를 쓴다. 그래서 한글이 `\\uXXXX` 로
    escape 되고 구분자에 공백이 들어간다. 프레임 헤더(`ensure_ascii=False` + 압축)와
    다르므로, 안드로이드가 프레임 규칙만 보고 파서를 만들면 여기서 어긋난다.
    """
    fields = {
        "v": config.PROTOCOL_VERSION,
        "id": "a1b2c3d4e5f60718",
        "name": "거실 맥북",
        "port": 9603,
        "platform": "darwin",
    }
    # discovery.py 의 _announce_loop 와 **똑같이** 만든다
    payload = json.dumps(fields).encode("utf-8")

    (OUT_DIR / "announce.bin").write_bytes(payload)
    (OUT_DIR / "announce.json").write_text(
        json.dumps(fields, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    assert b"\\uac70" in payload, "한글이 escape 되지 않았다. 규칙이 바뀌었는지 확인하라"
    print("%s  (%d 바이트)" % (OUT_DIR / "announce.bin", len(payload)))
    print("%s" % (OUT_DIR / "announce.json"))


# 원격 경로 해석은 보안 경계다. 파이썬 결과를 그대로 박아 안드로이드와 대조한다.
# 여기가 갈리면 공유 밖이 열리거나 멀쩡한 경로가 막힌다.
PATHS = [
    ".", "", "/", "//", "./", "./.",
    "Downloads",
    "프로젝트/src/main.py",
    "이름에 공백/있는 폴더/파일 이름.txt",
    "a/b/c",
    "a//b///c",
    "a/./b",
    "a/../b",
    "a/b/../..",
    "a/b/../../c",
    "..",
    "../..",
    "../../../../..",
    "a/../../..",
    "../../b",
    "a/b/../../../../c",
    r"a\b\c",
    r"a\b/c",
    r"..\..\..",
    r"공유\..\밖",
    "trailing/",
    "trailing//",
    "/leading",
    "//leading",
    "...",
    "....",
    "a/.../b",
    "공유/..",
    "공유/../..",
]


def write_paths():
    """`_split` 과 그 파생 함수들의 결과를 뽑는다."""
    rows = []
    for text in PATHS:
        rows.append(
            {
                "input": text,
                "parts": shares._split(text),
                "normalized": shares.normalize(text),
                "parent": shares.parent(text),
                "display": shares.display(text),
                "isRoot": shares._split(text) == [],
            }
        )

    (OUT_DIR / "paths.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("%s  (%d 가지)" % (OUT_DIR / "paths.json", len(rows)))


def write_fingerprints():
    """지문 벡터. 순서를 뒤집어도 같은 값이 나오는지가 핵심이다."""
    # 인증서는 DER 이라 어떤 바이트든 나온다. 문자열로 다루는 구현을 잡으려면
    # 0x00~0xFF 가 전부 들어간 벡터가 있어야 한다
    every_byte = bytes(range(256))
    long_one = bytes((i * 7 + 13) % 256 for i in range(1024))

    pairs = [
        ("짧은 것 둘", b"cert-a", b"cert-b"),
        ("순서를 뒤집었다 — 같은 값이어야 한다", b"cert-b", b"cert-a"),
        ("0x00~0xFF 전부", every_byte, b"cert-b"),
        ("양쪽 다 0x00~0xFF", every_byte, every_byte),
        ("같은 인증서 둘", b"cert-a", b"cert-a"),
        ("1바이트만 다르다 — 값이 완전히 달라야 한다", b"cert-a", b"cert-c"),
        ("DER 만한 길이", long_one, every_byte),
        ("한 바이트짜리", b"\x00", b"\xff"),
    ]

    out = [
        {
            "note": note,
            "certA": a.hex(),
            "certB": b.hex(),
            "fingerprint": fp_mod.fingerprint(a, b),
        }
        for note, a, b in pairs
    ]

    (OUT_DIR / "fingerprints.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("%s  (%d 쌍)" % (OUT_DIR / "fingerprints.json", len(out)))


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    stream = bytearray()
    manifest = []

    for note, header, blob in CASES:
        stream.extend(protocol.encode(header, blob))
        manifest.append(
            {
                "note": note,
                # 리더가 blob 키를 꺼내 없애므로 기대 헤더에도 넣지 않는다
                "header": header,
                "blobLen": len(blob) if blob else 0,
                "blobSha256": hashlib.sha256(blob).hexdigest() if blob else None,
            }
        )

    (OUT_DIR / "frames.bin").write_bytes(bytes(stream))
    (OUT_DIR / "frames.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print("%s  (%d 프레임, %d 바이트)" % (OUT_DIR / "frames.bin", len(manifest), len(stream)))
    print("%s" % (OUT_DIR / "frames.json"))

    write_announce()
    write_paths()
    write_fingerprints()


if __name__ == "__main__":
    main()
