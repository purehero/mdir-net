# Windows 방화벽 규칙 조회와 UAC 요청 — PowerShell 을 흉내내는 대역으로 확인한다

import json
import os
import tempfile
import unittest
from pathlib import Path

from mdirnet import config, winfw
from mdirnet.ui import app as app_module

from .helpers import make_shares


def canned(allow=2, blocks=(), raw=None):
    """PowerShell 자리에 끼울 대역. 우리가 정한 답만 돌려준다."""

    def runner(command):
        if raw is not None:
            return raw
        return json.dumps({"allow": allow, "blocks": list(blocks)})

    return runner


class TestInspect(unittest.TestCase):
    def test_two_allow_rules_and_no_blocks_is_ok(self):
        state = winfw.inspect(canned(allow=2))
        self.assertTrue(state.ok)

    def test_missing_rules_are_reported(self):
        state = winfw.inspect(canned(allow=1))
        self.assertTrue(state.allow_missing)
        self.assertFalse(state.ok)

    def test_a_block_rule_makes_it_not_ok_even_with_allow_rules(self):
        """Windows 는 차단을 허용보다 먼저 본다. 허용만 넣어도 소용이 없다."""
        state = winfw.inspect(canned(allow=2, blocks=["Python blocked"]))
        self.assertFalse(state.ok)
        self.assertEqual(state.blocks, ["Python blocked"])

    def test_a_single_block_arrives_unwrapped(self):
        """ConvertTo-Json 은 항목이 하나면 배열을 벗긴다."""
        state = winfw.inspect(canned(raw='{"allow":2,"blocks":"하나뿐"}'))
        self.assertEqual(state.blocks, ["하나뿐"])

    def test_garbage_is_unknown(self):
        for raw in ("", "not json", "[1,2]", '{"allow":"많음"}'):
            with self.assertRaises(winfw.Unknown, msg=raw):
                winfw.inspect(canned(raw=raw))

    def test_a_failing_runner_is_unknown(self):
        def boom(command):
            raise winfw.Unknown("powershell 이 없습니다")

        with self.assertRaises(winfw.Unknown):
            winfw.inspect(boom)


class TestAllowCommand(unittest.TestCase):
    def test_it_asks_for_elevation(self):
        command = " ".join(winfw.allow_command(script="C:\\mdirnet\\allow.ps1"))
        self.assertIn("Start-Process", command)
        self.assertIn("-Verb RunAs", command)
        self.assertIn("allow.ps1", command)

    def test_remove_blocks_is_passed_through_only_when_asked(self):
        with_blocks = " ".join(winfw.allow_command("a.ps1", remove_blocks=True))
        without = " ".join(winfw.allow_command("a.ps1", remove_blocks=False))
        self.assertIn("-RemoveBlocks", with_blocks)
        self.assertNotIn("-RemoveBlocks", without)

    def test_quotes_in_the_path_are_escaped(self):
        """경로에 따옴표가 있어도 명령이 깨지면 안 된다."""
        command = " ".join(winfw.allow_command("C:\\it's here\\allow.ps1"))
        self.assertIn("it''s here", command)

    def test_a_path_with_spaces_keeps_its_own_quotes(self):
        """ArgumentList 는 공백으로 이어붙이기만 한다. 경로가 쪼개지면 안 된다."""
        command = " ".join(winfw.allow_command("C:\\내 문서\\allow.ps1"))
        self.assertIn('\'"C:\\내 문서\\allow.ps1"\'', command)

    def test_the_bundled_script_exists(self):
        self.assertTrue(Path(winfw.script_path()).is_file(), winfw.script_path())

    def test_request_reports_whether_it_could_ask(self):
        sent = []
        self.assertTrue(winfw.request("a.ps1", runner=lambda c: sent.append(c) or ""))
        self.assertEqual(len(sent), 1)

        def boom(command):
            raise winfw.Unknown("nope")

        self.assertFalse(winfw.request("a.ps1", runner=boom))


class FirewallPromptTestCase(unittest.TestCase):
    """앱이 언제 묻고 언제 잠자코 있는지."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

        self.app = app_module.App(
            my_name="fw-node",
            shares=make_shares(self.root),
            persist=False,
            tcp_port=0,
            udp_port=0,
            platform_name="windows",
            use_discovery=False,
        )
        self.addCleanup(self.app.shutdown)

        self.requested = []
        self.state = winfw.State(0, [])
        real_inspect, real_request = winfw.inspect, winfw.request
        winfw.inspect = lambda: self.state
        winfw.request = lambda remove_blocks=False: (
            self.requested.append(remove_blocks) or True
        )
        self.addCleanup(lambda: setattr(winfw, "inspect", real_inspect))
        self.addCleanup(lambda: setattr(winfw, "request", real_request))

        self.app._firewall_asked = False  # 맥에서도 Windows 경로를 밟게 한다

    def test_nothing_is_asked_on_this_os_by_default(self):
        """Windows 가 아니면 처음부터 물을 것이 없다."""
        fresh = app_module.App(
            my_name="x", shares=make_shares(self.root), persist=False,
            tcp_port=0, udp_port=0, platform_name="macos", use_discovery=False,
        )
        self.addCleanup(fresh.shutdown)
        self.assertEqual(fresh._firewall_asked, os.name != "nt")

    def test_it_asks_when_rules_are_missing(self):
        self.app._ask_firewall()
        self.assertIsInstance(self.app.modal, app_module.ConfirmDialog)
        rendered = "\n".join(self.app.modal.lines(70))
        self.assertIn(str(config.TCP_PORT), rendered)
        self.assertIn(str(config.UDP_PORT), rendered)

    def test_it_stays_quiet_when_everything_is_open(self):
        self.state = winfw.State(2, [])
        self.app._ask_firewall()
        self.assertIsNone(self.app.modal)

    def test_it_stays_quiet_when_the_state_is_unknown(self):
        def boom():
            raise winfw.Unknown("powershell 없음")

        winfw.inspect = boom
        self.app._ask_firewall()
        self.assertIsNone(self.app.modal)

    def test_block_rules_are_listed_before_being_removed(self):
        """무엇을 지울지 보여주지 않고 동의를 받으면 동의가 아니다."""
        self.state = winfw.State(2, ["Python 차단 1", "Python 차단 2"])
        self.app._ask_firewall()

        rendered = "\n".join(self.app.modal.lines(70))
        self.assertIn("Python 차단 1", rendered)
        self.assertIn("Python 차단 2", rendered)

    def test_agreeing_asks_for_elevation_and_removes_blocks(self):
        self.state = winfw.State(0, ["Python 차단"])
        self.app._ask_firewall()
        self.app._on_key("y")

        self.assertEqual(self.requested, [True], "차단 규칙 제거까지 함께 요청해야 한다")

    def test_agreeing_without_block_rules_does_not_remove_anything(self):
        self.app._ask_firewall()
        self.app._on_key("y")
        self.assertEqual(self.requested, [False])

    def test_declining_changes_nothing_and_points_at_the_script(self):
        self.app._ask_firewall()
        self.app._on_key("n")

        self.assertEqual(self.requested, [])
        self.assertIn("allow-firewall.ps1", self.app.status)

    def test_the_flag_is_set_whatever_happens(self):
        """루프는 이 표시를 보고 다시 묻는다. 거절했는데 매 초 다시 물으면 못 쓴다."""
        def boom():
            raise winfw.Unknown("powershell 없음")

        for setup in (lambda: None, lambda: setattr(winfw, "inspect", boom)):
            self.app._firewall_asked = False
            self.app.modal = None
            setup()
            self.app._ask_firewall()
            self.assertTrue(self.app._firewall_asked)


if __name__ == "__main__":
    unittest.main()
