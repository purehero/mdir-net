# 반대 방향 검증 — Kotlin 이 만든 프레임을 파이썬 FrameReader 가 읽어내는지
#
# 안드로이드 단위 테스트가 프레임을 내보내야 이 테스트가 돌아간다. 없으면 건너뛴다.
#   cd android && ./gradlew :app:testDebugUnitTest
#
# 헤더 바이트가 파이썬과 똑같기를 요구하지는 않는다. JSON 키 순서와 이스케이프는
# 구현마다 다르고 의미에 영향이 없다. 요구하는 것은 **읽어낸 결과가 같은가** 다.

import hashlib
import json
import unittest
from pathlib import Path

from mdirnet import protocol

_ROOT = Path(__file__).resolve().parent.parent
_GOLDEN = _ROOT / "android" / "app" / "src" / "test" / "resources" / "golden" / "frames.json"
_KOTLIN = _ROOT / "android" / "app" / "build" / "golden-out" / "kotlin-frames.bin"


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


if __name__ == "__main__":
    unittest.main()
