# run.sh 의 의존성 설치 물음
#
# 이 스크립트는 사용자가 앱을 만나기 **전에** 도는 유일한 코드다. 여기서 잘못되면
# 앱은 뜨지도 못하고, 화면에 남는 것은 셸 오류 한 줄뿐이라 원인을 알기 어렵다.
#
# 진짜 파이썬을 쓰지 않는다. PATH 앞에 가짜 `python3` 를 놓아 무엇이 어떤 인자로
# 불렸는지 기록하게 한다. 그래야 "정말 pip 을 불렀는가" 를 확인할 수 있다.

import os
import pty
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN_SH = ROOT / "run.sh"


class LauncherTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bin = Path(self.tmp.name) / "bin"
        self.bin.mkdir()
        self.log = Path(self.tmp.name) / "부른것.txt"

    def fake_python(self, has_cryptography=False, pip_fails=False, appears_after_pip=True):
        """가짜 python3. 무엇으로 불렸는지 적고 흉내만 낸다."""
        script = f"""#!/bin/sh
echo "$@" >> "{self.log}"

case "$1 $2" in
  "-c import cryptography")
        if [ -f "{self.tmp.name}/설치됨" ]; then exit 0; fi
        {"exit 0" if has_cryptography else "exit 1"}
        ;;
  "-m pip")
        {"exit 1" if pip_fails else f'{"touch " + self.tmp.name + "/설치됨" if appears_after_pip else ":"}'}
        exit 0
        ;;
  "-m mdirnet")
        echo "앱이 떴다"
        exit 0
        ;;
esac
exit 0
"""
        path = self.bin / "python3"
        path.write_text(script, encoding="utf-8")
        path.chmod(0o755)

    def env(self):
        env = dict(os.environ)
        env["PATH"] = "%s:%s" % (self.bin, env["PATH"])
        return env

    def called(self):
        return self.log.read_text(encoding="utf-8") if self.log.exists() else ""

    def run_on_a_terminal(self, answer):
        """진짜 tty 를 붙여 돌린다. 파이프로는 물음 자체가 나오지 않는다."""
        parent, child = pty.openpty()
        proc = subprocess.Popen(
            ["/bin/sh", str(RUN_SH)],
            stdin=child,
            stdout=child,
            stderr=child,
            env=self.env(),
            cwd=self.tmp.name,
        )
        os.close(child)
        os.write(parent, answer.encode())

        out = bytearray()
        try:
            while True:
                chunk = os.read(parent, 4096)
                if not chunk:
                    break
                out.extend(chunk)
        except OSError:
            pass  # 상대가 닫으면 여기서 끝난다
        finally:
            os.close(parent)

        proc.wait(timeout=20)
        return proc.returncode, out.decode("utf-8", "replace")


class TestAsking(LauncherTestCase):
    def test_yes_installs_and_then_starts_the_app(self):
        self.fake_python()

        code, out = self.run_on_a_terminal("y\n")

        self.assertIn("지금 설치할까요?", out)
        self.assertIn("-m pip install", self.called(), "pip 을 부르지 않았다")
        self.assertIn("-m mdirnet", self.called(), "설치 후 앱을 띄우지 않았다")
        self.assertEqual(code, 0)

    def test_no_stops_without_installing(self):
        self.fake_python()

        code, out = self.run_on_a_terminal("n\n")

        self.assertNotIn("-m pip install", self.called(), "거절했는데 설치했다")
        self.assertNotIn("-m mdirnet", self.called(), "암호화 없이 앱을 띄웠다")
        self.assertIn("설치하지 않았습니다", out)
        self.assertNotEqual(code, 0)

    def test_just_pressing_enter_is_a_no(self):
        """되묻지 않고 넘어가는 기본값이 '설치함' 이면 곤란하다."""
        self.fake_python()

        code, _ = self.run_on_a_terminal("\n")

        self.assertNotIn("-m pip install", self.called())
        self.assertNotEqual(code, 0)

    def test_a_failed_install_says_what_to_try(self):
        self.fake_python(pip_fails=True)

        code, out = self.run_on_a_terminal("y\n")

        self.assertIn("설치하지 못했습니다", out)
        self.assertIn("--user", out, "다음에 뭘 해볼지 알려주지 않았다")
        self.assertNotEqual(code, 0)

    def test_pip_succeeding_into_the_wrong_place_is_caught(self):
        """pip 이 0 을 돌려줘도 이 파이썬에서 안 보일 수 있다."""
        self.fake_python(appears_after_pip=False)

        code, out = self.run_on_a_terminal("y\n")

        self.assertIn("보이지 않습니다", out)
        self.assertNotIn("-m mdirnet", self.called(), "안 되는데 앱을 띄웠다")
        self.assertNotEqual(code, 0)


class TestNotAsking(LauncherTestCase):
    def test_it_does_not_ask_when_nobody_can_answer(self):
        """파이프나 스크립트 안에서는 물어봐야 답할 사람이 없다."""
        self.fake_python()

        proc = subprocess.run(
            ["/bin/sh", str(RUN_SH)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            env=self.env(),
            cwd=self.tmp.name,
            timeout=20,
        )
        out = proc.stdout.decode() + proc.stderr.decode()

        self.assertNotIn("설치할까요", out)
        self.assertIn("pip install", out, "무엇을 하라는 안내는 남아야 한다")
        self.assertNotIn("-m pip install", self.called())
        self.assertNotEqual(proc.returncode, 0)

    def test_nothing_is_asked_when_it_is_already_there(self):
        self.fake_python(has_cryptography=True)

        code, out = self.run_on_a_terminal("")

        self.assertNotIn("설치할까요", out)
        self.assertIn("-m mdirnet", self.called())
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
