# PROTOCOL.md 가 실제 구현과 어긋나지 않는지 확인 — 구현체가 둘이라 문서가 기준이다

import re
import unittest
from pathlib import Path

from mdirnet import config, session

_DOC = Path(__file__).resolve().parent.parent / "PROTOCOL.md"


def _doc():
    return _DOC.read_text(encoding="utf-8")


class TestConstantTable(unittest.TestCase):
    """1절 상수표. 여기가 어긋나면 새 구현체가 잘못된 값으로 만들어진다."""

    NAMES = (
        "PROTOCOL_VERSION",
        "TCP_PORT",
        "UDP_PORT",
        "ANNOUNCE_INTERVAL",
        "PEER_TTL",
        "CHUNK_SIZE",
        "MAX_VIEW_BYTES",
        "MAX_HEADER_BYTES",
        "MAX_BLOB_BYTES",
        "CONNECT_TIMEOUT",
        "APPROVAL_TIMEOUT",
        "REQUEST_TIMEOUT",
    )

    def _cell(self, doc, name):
        """`| `NAME` | `VALUE` |` 행에서 값 칸을 꺼낸다."""
        found = re.search(r"\|\s*`%s`\s*\|\s*`([^`]+)`" % re.escape(name), doc)
        self.assertIsNotNone(found, "상수표에 %s 행이 없습니다" % name)
        return found.group(1)

    def test_every_constant_is_documented_with_the_real_value(self):
        doc = _doc()
        for name in self.NAMES:
            with self.subTest(name):
                cell = self._cell(doc, name)
                # `262144` (256KB) 처럼 뒤에 설명이 붙는다. 앞의 숫자만 본다
                number = re.match(r"([0-9]+(?:\.[0-9]+)?)", cell)
                self.assertIsNotNone(number, "%s 값 칸에 숫자가 없습니다: %r" % (name, cell))
                self.assertEqual(
                    float(number.group(1)),
                    float(getattr(config, name)),
                    "%s 가 config.py 와 다릅니다" % name,
                )

    def test_no_constant_is_left_out(self):
        """config.py 에 상수를 더하고 문서를 잊는 것을 잡는다."""
        documented = set(self.NAMES)
        # 선 위와 무관한 것. 창 크기는 내 터미널 이야기이지 상대와 맞출 값이 아니다
        skip = {"APP_NAME", "UI_TICK", "WINDOW_COLS", "WINDOW_ROWS"}
        actual = {
            name
            for name in dir(config)
            if name.isupper() and not name.startswith("_") and name not in skip
        }
        self.assertEqual(actual - documented, set(), "문서에 없는 상수가 있습니다")


class TestRequestTypes(unittest.TestCase):
    """5.3절 요청 목록. 여기가 빠지면 상대 구현이 후속 프레임으로 오해한다."""

    def test_documented_list_matches_the_dispatch_table(self):
        doc = _doc()
        found = re.search(r"```\n(home\s+list[^`]*?)\n```", doc)
        self.assertIsNotNone(found, "5.3절의 요청 목록 코드블록을 찾지 못했습니다")
        self.assertEqual(set(found.group(1).split()), set(session._REQUESTS))


class TestPortRange(unittest.TestCase):
    """8절. 실제 재시도 횟수와 문서의 상한이 맞아야 한다."""

    def test_upper_bound_matches_the_bind_retry_count(self):
        source = Path(session.__file__).read_text(encoding="utf-8")
        retries = re.search(r"for offset in range\((\d+)\)", source)
        self.assertIsNotNone(retries, "bind 재시도 루프를 찾지 못했습니다")
        last = config.TCP_PORT + int(retries.group(1)) - 1
        self.assertIn(str(last), _doc(), "문서의 포트 상한이 %d 이어야 합니다" % last)


if __name__ == "__main__":
    unittest.main()


class TestTheCopyIsInSync(unittest.TestCase):
    """`PROTOCOL.md` 는 두 저장소에 같은 내용으로 있다.

    한쪽만 고치면 두 구현이 다른 규격을 보고 만들어진다. 아직 한 저장소에 같이
    있는 동안은 여기서 잡는다. 나눈 뒤에는 이 검사가 사라지므로 사람이 지켜야 한다.
    """

    COPY = Path(__file__).resolve().parent.parent / "android" / "PROTOCOL.md"

    def test_the_android_copy_matches(self):
        if not self.COPY.is_file():
            self.skipTest("안드로이드 사본이 없습니다 (저장소를 나눈 뒤다)")

        self.assertEqual(
            _doc(),
            self.COPY.read_text(encoding="utf-8"),
            "PROTOCOL.md 사본이 갈렸습니다. 양쪽을 같게 맞추세요",
        )
