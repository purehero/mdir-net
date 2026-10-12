# aws CLI 의 `~/.aws` 설정을 읽는 부분의 테스트
#
# 실제 홈의 `~/.aws` 를 절대 읽지 않는다. 환경변수로 임시 파일을 물린다 -
# 테스트가 사람의 자격증명을 읽으면 안 되고, 그 사람이 어떤 프로필을 갖고
# 있느냐에 따라 통과가 갈리면 테스트가 아니다.

import os
import tempfile
import unittest
from pathlib import Path

from mdirnet import awscfg


class AwsConfigTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.creds = self.dir / "credentials"
        self.conf = self.dir / "config"
        self._env(AWS_SHARED_CREDENTIALS_FILE=str(self.creds),
                  AWS_CONFIG_FILE=str(self.conf), AWS_PROFILE=None)

    def _env(self, **pairs):
        for key, value in pairs.items():
            before = os.environ.get(key)
            self.addCleanup(self._put, key, before)
            self._put(key, value)

    @staticmethod
    def _put(key, value):
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    def write(self, creds="", conf=""):
        self.creds.write_text(creds, encoding="utf-8")
        self.conf.write_text(conf, encoding="utf-8")

    def names(self):
        return [p.name for p in awscfg.profiles()]


class TestReadingProfiles(AwsConfigTestCase):
    def test_no_files_means_no_profiles(self):
        self.assertEqual(awscfg.profiles(), [])
        self.assertFalse(awscfg.available())

    def test_static_keys_are_usable(self):
        self.write(creds="""
[default]
aws_access_key_id = AKIADEFAULT
aws_secret_access_key = 비밀1
""")
        found = awscfg.profiles()
        self.assertEqual([p.name for p in found], ["default"])
        self.assertTrue(found[0].usable)
        self.assertEqual(found[0].access_key, "AKIADEFAULT")
        self.assertEqual(found[0].secret_key, "비밀1")

    def test_the_config_file_supplies_the_region(self):
        """리전은 config 에, 키는 credentials 에 있는 것이 보통이다."""
        self.write(
            creds="[일하는계정]\naws_access_key_id = AK\naws_secret_access_key = S\n",
            conf="[profile 일하는계정]\nregion = ap-northeast-2\n",
        )
        self.assertEqual(awscfg.get("일하는계정").region, "ap-northeast-2")

    def test_the_default_section_has_no_profile_prefix(self):
        """config 에서 기본 프로필만 `[default]` 다. 나머지는 `[profile 이름]`."""
        self.write(conf="[default]\nregion = us-east-1\n")
        self.assertEqual(self.names(), ["default"])
        self.assertEqual(awscfg.get("default").region, "us-east-1")

    def test_credentials_wins_over_config(self):
        self.write(
            creds="[default]\naws_access_key_id = 진짜\naws_secret_access_key = S\n",
            conf="[default]\naws_access_key_id = 낡은것\n",
        )
        self.assertEqual(awscfg.get("default").access_key, "진짜")

    def test_a_session_token_is_carried_along(self):
        self.write(creds="""
[임시]
aws_access_key_id = AK
aws_secret_access_key = S
aws_session_token = 토큰
""")
        self.assertEqual(awscfg.get("임시").session_token, "토큰")

    def test_default_comes_first_then_the_rest_by_name(self):
        self.write(creds="""
[zulu]
aws_access_key_id = A
aws_secret_access_key = B
[alpha]
aws_access_key_id = A
aws_secret_access_key = B
[default]
aws_access_key_id = A
aws_secret_access_key = B
""")
        self.assertEqual(self.names(), ["default", "alpha", "zulu"])

    def test_a_broken_file_does_not_take_the_program_down(self):
        """설정이 깨졌다고 멈출 이유가 없다. 직접 입력으로 돌아가면 된다."""
        self.write(creds="이건 ini 가 아니다 [[[")
        self.assertEqual(awscfg.profiles(), [])


class TestWhatCannotBeUsed(AwsConfigTestCase):
    """쓸 수 없는 프로필도 목록에는 내보낸다.

    조용히 빼버리면 "내 프로필이 왜 없지" 가 된다. 왜 못 쓰는지 적어둔다.
    """

    def reason_for(self, body):
        self.write(conf=body)
        return awscfg.get("일하는계정").reason

    def test_sso_is_listed_with_a_reason(self):
        reason = self.reason_for(
            "[profile 일하는계정]\nsso_start_url = https://example.awsapps.com/start\n"
        )
        self.assertIn("SSO", reason)

    def test_a_credentials_process_is_listed_with_a_reason(self):
        reason = self.reason_for(
            "[profile 일하는계정]\ncredentials_process = /usr/bin/어떤것\n"
        )
        self.assertIn("외부 명령", reason)

    def test_assume_role_is_listed_with_a_reason(self):
        reason = self.reason_for(
            "[profile 일하는계정]\nrole_arn = arn:aws:iam::1:role/R\nsource_profile = default\n"
        )
        self.assertIn("역할", reason)

    def test_a_profile_with_nothing_useful_says_so(self):
        self.assertIn("키가 없는", self.reason_for("[profile 일하는계정]\nregion = us-east-1\n"))

    def test_static_keys_win_even_when_other_keys_are_present(self):
        """키가 실제로 있으면 그것으로 붙는다. aws CLI 도 그렇게 한다."""
        self.write(conf="""
[profile 일하는계정]
role_arn = arn:aws:iam::1:role/R
aws_access_key_id = AK
aws_secret_access_key = S
""")
        self.assertTrue(awscfg.get("일하는계정").usable)


class TestPreferred(AwsConfigTestCase):
    def test_nothing_at_all_gives_an_empty_name(self):
        self.assertEqual(awscfg.preferred(), "")

    def test_aws_profile_is_honoured(self):
        self.write(creds="[default]\naws_access_key_id=A\naws_secret_access_key=B\n"
                         "[다른것]\naws_access_key_id=A\naws_secret_access_key=B\n")
        self._env(AWS_PROFILE="다른것")
        self.assertEqual(awscfg.preferred(), "다른것")

    def test_an_unknown_aws_profile_falls_back(self):
        self.write(creds="[default]\naws_access_key_id=A\naws_secret_access_key=B\n")
        self._env(AWS_PROFILE="없는것")
        self.assertEqual(awscfg.preferred(), "default")

    def test_it_prefers_one_that_can_actually_be_used(self):
        self.write(conf="[default]\nsso_start_url = https://x\n",
                   creds="[쓸수있는것]\naws_access_key_id=A\naws_secret_access_key=B\n")
        self.assertEqual(awscfg.preferred(), "쓸수있는것")


if __name__ == "__main__":
    unittest.main()
