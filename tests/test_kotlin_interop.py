# 반대 방향 검증 — Kotlin 이 만든 바이트를 파이썬 구현이 읽어내는지
#
# 안드로이드 단위 테스트가 먼저 바이트를 내보내야 이 테스트가 돌아간다. 없으면 건너뛴다.
#   cd android && ./gradlew :app:testDebugUnitTest
#
# 바이트가 파이썬과 똑같기를 요구하지는 않는다. JSON 키 순서와 이스케이프는 구현마다
# 다르고 의미에 영향이 없다. 요구하는 것은 **읽어낸 결과가 같은가** 다.

import hashlib
import json
import unittest
from pathlib import Path

from mdirnet import discovery, protocol

_ROOT = Path(__file__).resolve().parent.parent
_GOLDEN_DIR = _ROOT / "android" / "app" / "src" / "test" / "resources" / "golden"
_OUT_DIR = _ROOT / "android" / "app" / "build" / "golden-out"

_GOLDEN = _GOLDEN_DIR / "frames.json"
_KOTLIN = _OUT_DIR / "kotlin-frames.bin"
_KOTLIN_ANNOUNCE = _OUT_DIR / "kotlin-announce.bin"


class _BytesAsSocket:
    """FrameReader 는 recv 를 가진 객체를 기대한다. 바이트열을 그렇게 보이게 감싼다."""

    def __init__(self, data):
        self._data = data
        self._at = 0

    def recv(self, want):
        chunk = self._data[self._at : self._at + want]
        self._at += len(chunk)
        return chunk

    @property
    def exhausted(self):
        return self._at >= len(self._data)


class TestKotlinFrames(unittest.TestCase):
    def setUp(self):
        if not _KOTLIN.exists():
            self.skipTest(
                "%s 가 없습니다. `cd android && ./gradlew :app:testDebugUnitTest` 를 "
                "먼저 실행하세요" % _KOTLIN
            )
        self.expected = json.loads(_GOLDEN.read_text(encoding="utf-8"))

    def test_python_reads_every_frame_kotlin_wrote(self):
        sock = _BytesAsSocket(_KOTLIN.read_bytes())
        reader = protocol.FrameReader(sock)

        for index, case in enumerate(self.expected):
            with self.subTest(index=index, note=case["note"]):
                header, blob = reader.read()

                self.assertEqual(header, case["header"], "헤더가 다릅니다")
                self.assertNotIn("blob", header, "리더가 blob 키를 없애야 합니다")

                if case["blobLen"] == 0:
                    self.assertIsNone(blob, "blob 이 없어야 합니다")
                else:
                    self.assertEqual(len(blob), case["blobLen"])
                    self.assertEqual(hashlib.sha256(blob).hexdigest(), case["blobSha256"])

        self.assertTrue(sock.exhausted, "읽고 남은 바이트가 있습니다")

    def test_the_stream_ends_where_it_should(self):
        """프레임 수가 맞아야 한다. 하나 덜 쓰거나 더 쓰면 여기서 걸린다."""
        sock = _BytesAsSocket(_KOTLIN.read_bytes())
        reader = protocol.FrameReader(sock)

        count = 0
        while True:
            try:
                reader.read()
            except protocol.PeerClosed:
                break
            count += 1

        self.assertEqual(count, len(self.expected))


class TestKotlinAnnounce(unittest.TestCase):
    """탐색 알림 — 진짜 Discovery 객체에 통과시켜 본다.

    파서만 따로 부르지 않고 `_on_packet` 을 쓴다. 버전 검사, id 검사, 자기 패킷
    걸러내기까지 실제 경로를 그대로 지나야 의미가 있다.
    """

    def setUp(self):
        if not _KOTLIN_ANNOUNCE.exists():
            self.skipTest(
                "%s 가 없습니다. `cd android && ./gradlew :app:testDebugUnitTest` 를 "
                "먼저 실행하세요" % _KOTLIN_ANNOUNCE
            )
        self.payload = _KOTLIN_ANNOUNCE.read_bytes()

    def _discovery(self):
        return discovery.Discovery(
            node_id="ffffffffffffffff",  # Kotlin 이 쓴 것과 겹치지 않게
            name="맥",
            tcp_port=9600,
            platform="darwin",
        )

    def test_python_accepts_the_announcement(self):
        disco = self._discovery()
        disco._on_packet(self.payload, "192.168.0.42")

        peers = disco.peers()
        self.assertEqual(len(peers), 1, "파이썬이 안드로이드 알림을 버렸습니다")

        peer = peers[0]
        self.assertEqual(peer.name, "내 폰")
        self.assertEqual(peer.port, 9605)
        self.assertEqual(peer.platform, "android")
        # 주소는 페이로드가 아니라 출발지 IP 에서 온다
        self.assertEqual(peer.host, "192.168.0.42")

    def test_the_announcement_is_a_bare_json_object(self):
        """프레이밍이 붙으면 안 된다. 알림은 본문 전체가 JSON 객체 하나다."""
        parsed = json.loads(self.payload.decode("utf-8"))
        self.assertIsInstance(parsed, dict)
        self.assertEqual(parsed["v"], 1)

    def test_python_ignores_its_own_id(self):
        """자기 패킷 걸러내기가 살아있는지. 없으면 자기 자신이 목록에 뜬다."""
        node_id = json.loads(self.payload.decode("utf-8"))["id"]
        disco = discovery.Discovery(
            node_id=node_id, name="맥", tcp_port=9600, platform="darwin"
        )
        disco._on_packet(self.payload, "192.168.0.42")
        self.assertEqual(disco.peers(), [])


if __name__ == "__main__":
    unittest.main()
