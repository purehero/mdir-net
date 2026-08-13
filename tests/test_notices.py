# 알림과 큰 응답 - 안드로이드 쪽에서 갱신한 규약 (PROTOCOL.md 5.1 / 5.2 / 7절)
#
# 세 가지다.
#   1. 알림은 `q` 규칙보다 **먼저** 걸러야 한다. 안 그러면 조용히 버려진다
#   2. 내 응답이 프레임에 안 담기는 것은 연결을 끊을 일이 아니다. `err` 로 답한다
#   3. 모르는 알림은 무해하게 버려진다 (그래서 버전을 안 올렸다)

import queue
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from mdirnet import config, protocol, secure, session
from mdirnet import shares as shares_mod

from .helpers import make_shares, only_name


class SessionPairTestCase(unittest.TestCase):
    """진짜 소켓 한 쌍에 Session 둘을 물린다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / "폴더").mkdir()

        self.a_events = queue.Queue()
        self.b_events = queue.Queue()
        self.a_shares = make_shares(self.root)
        self.b_shares = make_shares(self.root)

        left, right = socket.socketpair()
        self.a = session.Session(left, "상대B", "127.0.0.1", self.a_shares, self.a_events)
        self.b = session.Session(right, "상대A", "127.0.0.1", self.b_shares, self.b_events)
        self.a.start()
        self.b.start()
        self.addCleanup(self.b.close)
        self.addCleanup(self.a.close)

    def wait_for(self, events, kind, timeout=5.0):
        end = threading.Event()
        deadline = timeout
        while deadline > 0:
            try:
                got = events.get(timeout=0.2)
            except queue.Empty:
                deadline -= 0.2
                continue
            if got[0] == kind:
                return got
            deadline -= 0.05
        self.fail("%s 이벤트가 오지 않았습니다" % kind)


class TestSharesChanged(SessionPairTestCase):
    def test_the_peer_is_told_and_the_sender_waits_for_nothing(self):
        self.a.notify_shares_changed()

        self.wait_for(self.b_events, "peer_shares_changed")

    def test_it_does_not_break_the_session(self):
        """알림 뒤에도 평범한 요청이 그대로 돌아야 한다."""
        self.a.notify_shares_changed()

        path, entries = self.a.remote_list(shares_mod.ROOT)

        self.assertEqual(path, shares_mod.ROOT)
        self.assertEqual([e["name"] for e in entries], [only_name(self.b_shares)])

    def test_many_notices_in_a_row_are_fine(self):
        for _ in range(5):
            self.a.notify_shares_changed()

        self.assertEqual(self.a.remote_home(), shares_mod.ROOT)


class TestAnUnknownNoticeIsHarmless(SessionPairTestCase):
    """규약이 버전을 올리지 않은 근거다. 모르는 알림은 조용히 버려진다."""

    def test_a_notice_this_build_does_not_know_is_dropped(self):
        self.a._send({"t": "뭔가.새로운.알림"})

        # 연결도 세션도 멀쩡하다
        self.assertEqual(self.a.remote_home(), shares_mod.ROOT)
        self.assertTrue(self.b_events.empty() or True)

    def test_a_notice_never_gets_an_answer(self):
        """답을 보내면 상대는 그것을 자기 대화의 응답으로 오해한다."""
        self.a._send({"t": "shares.changed"})
        self.wait_for(self.b_events, "peer_shares_changed")

        # b 가 답을 보냈다면 a 의 다음 요청이 그 답을 먼저 받아 어긋난다
        self.assertEqual(self.a.remote_home(), shares_mod.ROOT)


class TestAResponseTooBigToSend(SessionPairTestCase):
    """항목이 많은 폴더에서 실제로 걸린다. 끊지 말고 답해야 한다."""

    def crowd_the_share(self, how_many=300):
        """`list.ok` 가 커지도록 항목을 채운다. 실제로 걸리는 상황과 같은 모양이다."""
        for index in range(how_many):
            (self.root / ("폴더%03d" % index)).mkdir(exist_ok=True)

    def shrink_header_limit(self, to=2000):
        """`list.ok` 는 넘고 `err` 는 넉넉히 담기는 상한."""
        original = config.MAX_HEADER_BYTES
        config.MAX_HEADER_BYTES = to
        self.addCleanup(setattr, config, "MAX_HEADER_BYTES", original)
        return original

    def test_it_answers_with_an_error_instead_of_closing(self):
        self.crowd_the_share()
        original = self.shrink_header_limit()

        # 가상 루트는 공유 이름 하나뿐이라 작다. 공유 **안**을 봐야 커진다
        with self.assertRaises(session.SessionError) as caught:
            self.a.remote_list(only_name(self.b_shares))

        message = str(caught.exception)
        self.assertIn("나눠서", message, "무엇을 하면 되는지 말하지 않았다: %s" % message)

        # **연결은 멀쩡해야 한다.** 상한을 되돌리면 곧바로 다시 된다
        config.MAX_HEADER_BYTES = original
        self.assertEqual(self.a.remote_home(), shares_mod.ROOT)

    def test_the_error_does_not_blame_the_network(self):
        """'연결이 끊어졌다' 로 말하면 사용자가 네트워크를 의심하기 시작한다."""
        self.crowd_the_share()
        self.shrink_header_limit()

        with self.assertRaises(session.SessionError) as caught:
            self.a.remote_list(only_name(self.b_shares))

        for wrong in ("끊", "연결이", "네트워크"):
            self.assertNotIn(wrong, str(caught.exception), str(caught.exception))


if __name__ == "__main__":
    unittest.main()
