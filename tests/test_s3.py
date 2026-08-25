# S3 서명과 REST 호출, 그리고 Commands 메뉴의 S3 연결을 확인하는 테스트
#
# 실제 AWS 로 나가지 않는다. 전송 계층을 가짜로 갈아끼워 요청이 어떻게 만들어지고
# 응답이 어떻게 항목으로 바뀌는지만 본다. 서명만은 AWS 문서의 공식 예제 값과
# 글자 하나까지 맞춰본다 - 서명이 틀리면 나머지가 다 맞아도 아무것도 못 한다.

import datetime
import json
import tempfile
import threading
import unittest
from pathlib import Path

from mdirnet import s3, session, settings
from mdirnet import shares as shares_mod
from mdirnet.ui import app as app_module
from mdirnet.ui import term

from .helpers import make_shares

NS = "http://s3.amazonaws.com/doc/2006-01-01/"

# AWS 문서 "Signature Calculations for the Authorization Header" 의 예제 자격증명
EXAMPLE_KEY = "AKIAIOSFODNN7EXAMPLE"
EXAMPLE_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
EXAMPLE_HOST = "examplebucket.s3.amazonaws.com"
EXAMPLE_TIME = datetime.datetime(2013, 5, 24, tzinfo=datetime.timezone.utc)


def signature_of(headers):
    return headers["Authorization"].rsplit("Signature=", 1)[1]


class TestSigV4(unittest.TestCase):
    """AWS 가 문서에 박아둔 예제 네 개를 그대로 재현한다.

    64자리 16진수가 우연히 맞을 수는 없다. 하나라도 맞으면 정규 요청, 서명 대상
    문자열, 키 파생이 모두 문서와 같다는 뜻이다.
    """

    def sign(self, method, path, query, headers, payload_hash=s3.EMPTY_SHA256):
        return signature_of(
            s3.sign(method, EXAMPLE_HOST, path, query, headers, payload_hash,
                    EXAMPLE_KEY, EXAMPLE_SECRET, "us-east-1", EXAMPLE_TIME)
        )

    def test_get_object_with_a_range_header(self):
        self.assertEqual(
            self.sign("GET", "/test.txt", None, {"range": "bytes=0-9"}),
            "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41",
        )

    def test_get_with_a_valueless_query_parameter(self):
        self.assertEqual(
            self.sign("GET", "/", {"lifecycle": ""}, {}),
            "fea454ca298b7da1c68078a5d1bdbfbbe0d65c699e0f91ac7a200a0136783543",
        )

    def test_get_with_sorted_query_parameters(self):
        self.assertEqual(
            self.sign("GET", "/", {"max-keys": "2", "prefix": "J"}, {}),
            "34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7",
        )

    def test_put_object_with_a_special_character_in_the_key(self):
        self.assertEqual(
            self.sign(
                "PUT", "/test$file.text", None,
                {"date": "Fri, 24 May 2013 00:00:00 GMT",
                 "x-amz-storage-class": "REDUCED_REDUNDANCY"},
                "44ce7dd67c959e0d3524ffac1771dfbba87d2b6b4b4e99e42034a8b803f8b072",
            ),
            "98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd",
        )

    def test_signing_key_is_derived_per_day_and_region(self):
        """같은 비밀 키라도 날짜나 리전이 다르면 다른 키가 나온다."""
        a = s3.signing_key(EXAMPLE_SECRET, "20130524", "us-east-1")
        self.assertNotEqual(a, s3.signing_key(EXAMPLE_SECRET, "20130525", "us-east-1"))
        self.assertNotEqual(a, s3.signing_key(EXAMPLE_SECRET, "20130524", "ap-northeast-2"))

    def test_the_secret_key_never_appears_in_the_header(self):
        headers = s3.sign("GET", EXAMPLE_HOST, "/", None, {}, s3.EMPTY_SHA256,
                          EXAMPLE_KEY, EXAMPLE_SECRET, "us-east-1", EXAMPLE_TIME)
        self.assertNotIn(EXAMPLE_SECRET, json.dumps(headers))


class TestPathSpace(unittest.TestCase):
    """원격 경로는 피어 쪽과 똑같은 문자열 공간이다. 첫 조각이 버킷이다."""

    def test_root_has_no_bucket(self):
        self.assertEqual(s3.split(shares_mod.ROOT), ("", ""))

    def test_first_part_is_the_bucket(self):
        self.assertEqual(s3.split("my-bucket"), ("my-bucket", ""))
        self.assertEqual(s3.split("my-bucket/a/b.txt"), ("my-bucket", "a/b.txt"))

    def test_dotdot_cannot_escape_above_the_root(self):
        """`..` 을 아무리 쌓아도 버킷 목록 위로는 못 올라간다."""
        self.assertEqual(s3.split("bucket/../../../etc/passwd"), ("etc", "passwd"))
        self.assertEqual(s3.split("../.."), ("", ""))


# --- 가짜 전송 계층 ---


class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status = status
        self.headers = dict(headers or {})
        self._body = body
        self._at = 0
        self.closed = False

    def read(self, size=None):
        if size is None:
            chunk, self._at = self._body[self._at:], len(self._body)
            return chunk
        chunk = self._body[self._at:self._at + size]
        self._at += len(chunk)
        return chunk

    def close(self):
        self.closed = True


class FakeTransport:
    """요청을 기록하고 미리 정해둔 응답을 돌려준다.

    `replies` 는 (method, path 앞부분) 을 받아 FakeResponse 를 만드는 함수다.
    """

    def __init__(self, reply):
        self.calls = []
        self._reply = reply

    def __call__(self, host, method, path, headers, body, timeout):
        # 올리는 본문은 여기서 다 읽어들인다. 실제 http.client 가 하는 일이라
        # 진행률 콜백도 이때 불린다
        payload = b""
        if hasattr(body, "read"):
            while True:
                chunk = body.read(64 * 1024)
                if not chunk:
                    break
                payload += chunk
        elif body:
            payload = body

        self.calls.append({"host": host, "method": method, "path": path,
                           "headers": headers, "body": payload})
        return self._reply(method, path, payload)


def xml_listing(contents=(), prefixes=(), truncated=False, token=""):
    """ListObjectsV2 응답을 만든다. 네임스페이스까지 실제와 같게 둔다."""
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<ListBucketResult xmlns="%s">' % NS,
             "<IsTruncated>%s</IsTruncated>" % ("true" if truncated else "false")]
    if token:
        parts.append("<NextContinuationToken>%s</NextContinuationToken>" % token)
    for key, size, when in contents:
        parts.append(
            "<Contents><Key>%s</Key><Size>%d</Size><LastModified>%s</LastModified></Contents>"
            % (key, size, when)
        )
    for prefix in prefixes:
        parts.append("<CommonPrefixes><Prefix>%s</Prefix></CommonPrefixes>" % prefix)
    parts.append("</ListBucketResult>")
    return "".join(parts).encode("utf-8")


def link(reply, bucket=""):
    transport = FakeTransport(reply)
    made = s3.S3Link("ap-northeast-2", EXAMPLE_KEY, EXAMPLE_SECRET, bucket,
                     transport=transport, clock=lambda: EXAMPLE_TIME)
    return made, transport


class TestListing(unittest.TestCase):
    def test_root_lists_buckets(self):
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<ListAllMyBucketsResult xmlns="%s"><Buckets>'
            "<Bucket><Name>alpha</Name><CreationDate>2020-01-02T03:04:05.000Z</CreationDate></Bucket>"
            "<Bucket><Name>beta</Name><CreationDate>2021-01-02T03:04:05Z</CreationDate></Bucket>"
            "</Buckets></ListAllMyBucketsResult>" % NS
        ).encode("utf-8")
        made, _ = link(lambda m, p, b: FakeResponse(200, body))

        path, entries = made.remote_list(shares_mod.ROOT)
        self.assertEqual(path, shares_mod.ROOT)
        self.assertEqual([e["name"] for e in entries], ["alpha", "beta"])
        self.assertTrue(all(e["is_dir"] for e in entries))
        self.assertGreater(entries[0]["mtime"], 0)

    def test_a_fixed_bucket_skips_the_list_buckets_call(self):
        """버킷을 정해두면 ListBuckets 권한이 없어도 루트가 열린다."""
        made, transport = link(lambda m, p, b: FakeResponse(500, b""), bucket="only")

        _, entries = made.remote_list(shares_mod.ROOT)
        self.assertEqual([e["name"] for e in entries], ["only"])
        self.assertEqual(transport.calls, [], "네트워크를 건드렸다")

    def test_folder_listing_splits_prefixes_and_objects(self):
        body = xml_listing(
            contents=[("docs/a.txt", 5, "2024-03-04T05:06:07.000Z"),
                      ("docs/", 0, "2024-03-04T05:06:07.000Z")],
            prefixes=["docs/sub/"],
        )
        made, transport = link(lambda m, p, b: FakeResponse(200, body))

        path, entries = made.remote_list("bucket/docs")
        self.assertEqual(path, "bucket/docs")
        self.assertEqual(
            sorted((e["name"], e["is_dir"]) for e in entries),
            [("a.txt", False), ("sub", True)],
        )
        self.assertIn("prefix=docs%2F", transport.calls[0]["path"])
        self.assertIn("delimiter=%2F", transport.calls[0]["path"])

    def test_the_folder_placeholder_key_is_not_shown_as_an_entry(self):
        """`docs/` 는 폴더 자리표다. 이름 없는 항목으로 목록에 새면 안 된다."""
        body = xml_listing(contents=[("docs/", 0, "2024-03-04T05:06:07Z")])
        made, _ = link(lambda m, p, b: FakeResponse(200, body))

        _, entries = made.remote_list("bucket/docs")
        self.assertEqual(entries, [])

    def test_listing_follows_the_continuation_token(self):
        pages = [
            xml_listing(contents=[("a.txt", 1, "2024-01-01T00:00:00Z")],
                        truncated=True, token="NEXT"),
            xml_listing(contents=[("b.txt", 2, "2024-01-01T00:00:00Z")]),
        ]
        made, transport = link(lambda m, p, b: FakeResponse(200, pages[len(transport.calls) - 1]))

        _, entries = made.remote_list("bucket")
        self.assertEqual([e["name"] for e in entries], ["a.txt", "b.txt"])
        self.assertIn("continuation-token=NEXT", transport.calls[1]["path"])

    def test_unreadable_timestamps_do_not_lose_the_entry(self):
        body = xml_listing(contents=[("a.txt", 3, "언제였더라")])
        made, _ = link(lambda m, p, b: FakeResponse(200, body))

        _, entries = made.remote_list("bucket")
        self.assertEqual(entries[0]["name"], "a.txt")
        self.assertEqual(entries[0]["mtime"], 0.0)


class TestTree(unittest.TestCase):
    def test_tree_returns_files_and_every_folder_between_them(self):
        body = xml_listing(contents=[
            ("top/a.txt", 3, "2024-01-01T00:00:00Z"),
            ("top/x/y/b.txt", 4, "2024-01-01T00:00:00Z"),
            ("top/empty/", 0, "2024-01-01T00:00:00Z"),
        ])
        made, transport = link(lambda m, p, b: FakeResponse(200, body))

        files, dirs = made.remote_tree("bucket/top")
        self.assertEqual(sorted(f["path"] for f in files), ["a.txt", "x/y/b.txt"])
        self.assertEqual(dirs, ["empty", "x", "x/y"])
        self.assertNotIn("delimiter", transport.calls[0]["path"])

    def test_the_bucket_list_itself_cannot_be_copied(self):
        made, _ = link(lambda m, p, b: FakeResponse(200, b""))
        with self.assertRaises(s3.S3Error):
            made.remote_tree(shares_mod.ROOT)


class TestTransfer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_download_writes_the_file_and_reports_progress(self):
        payload = bytes(range(256)) * 100
        made, _ = link(lambda m, p, b: FakeResponse(200, payload))

        seen = []
        dest = self.root / "안쪽" / "받은.bin"
        made.pull_file("bucket/a.bin", dest, on_progress=seen.append)

        self.assertEqual(dest.read_bytes(), payload)
        self.assertEqual(sum(seen), len(payload))
        self.assertFalse(list(self.root.glob("**/*.part")), ".part 가 남았다")

    def test_a_failed_download_leaves_no_half_file(self):
        class Broken(FakeResponse):
            def read(self, size=None):
                raise OSError("끊겼다")

        made, _ = link(lambda m, p, b: Broken(200, b""))
        dest = self.root / "받은.bin"
        with self.assertRaises(OSError):
            made.pull_file("bucket/a.bin", dest)

        self.assertFalse(dest.exists())
        self.assertFalse(list(self.root.glob("**/*.part")))

    def test_upload_sends_the_bytes_and_reports_progress(self):
        source = self.root / "보낼.bin"
        source.write_bytes(b"z" * 5000)
        made, transport = link(lambda m, p, b: FakeResponse(200, b""))

        seen = []
        sent = made.push_file(source, "bucket/폴더/보낼.bin", on_progress=seen.append)

        self.assertEqual(sent, 5000)
        self.assertEqual(sum(seen), 5000)
        call = transport.calls[0]
        self.assertEqual(call["method"], "PUT")
        self.assertEqual(call["body"], b"z" * 5000)
        self.assertEqual(call["headers"]["content-length"], "5000")

    def test_upload_signs_the_body_as_unsigned_over_https(self):
        """본문 해시를 미리 구하려고 파일을 두 번 읽지 않는다."""
        source = self.root / "보낼.bin"
        source.write_bytes(b"z" * 10)
        made, transport = link(lambda m, p, b: FakeResponse(200, b""))
        made.push_file(source, "bucket/보낼.bin")

        self.assertEqual(
            transport.calls[0]["headers"]["x-amz-content-sha256"], s3.UNSIGNED_PAYLOAD
        )

    def test_a_file_over_five_gigabytes_is_refused_before_sending(self):
        source = self.root / "큰.bin"
        source.write_bytes(b"x")
        made, transport = link(lambda m, p, b: FakeResponse(200, b""))
        made.__dict__  # 크기만 속인다. 5GB 를 실제로 만들 수는 없다
        original = Path.stat

        class Huge:
            st_size = s3.MAX_PUT_BYTES + 1

        try:
            Path.stat = lambda self, *a, **k: Huge()
            with self.assertRaises(s3.S3Error) as caught:
                made.push_file(source, "bucket/큰.bin")
        finally:
            Path.stat = original

        self.assertIn("5GB", str(caught.exception))
        self.assertEqual(transport.calls, [], "보내지 말았어야 했다")

    def test_viewer_reads_only_the_head_and_learns_the_total(self):
        made, transport = link(
            lambda m, p, b: FakeResponse(
                206, b"0123456789", {"content-range": "bytes 0-9/4096"}
            )
        )
        data, total = made.remote_head("bucket/a.txt", 10)

        self.assertEqual(data, b"0123456789")
        self.assertEqual(total, 4096)
        self.assertEqual(transport.calls[0]["headers"]["range"], "bytes=0-9")


class TestMutations(unittest.TestCase):
    def test_mkdir_puts_an_empty_placeholder_object(self):
        made, transport = link(lambda m, p, b: FakeResponse(200, b""))
        made.remote_mkdir("bucket/새폴더")

        call = transport.calls[0]
        self.assertEqual(call["method"], "PUT")
        self.assertTrue(call["path"].startswith("/%EC%83%88%ED%8F%B4%EB%8D%94/"))
        self.assertEqual(call["body"], b"")

    def test_deleting_a_folder_removes_everything_under_it(self):
        body = xml_listing(contents=[
            ("top/", 0, "2024-01-01T00:00:00Z"),
            ("top/a.txt", 1, "2024-01-01T00:00:00Z"),
            ("top/sub/b.txt", 1, "2024-01-01T00:00:00Z"),
        ])
        made, transport = link(
            lambda m, p, b: FakeResponse(200, body if m == "GET" else b"")
        )
        made.remote_delete("bucket/top")

        deleted = [c["path"] for c in transport.calls if c["method"] == "DELETE"]
        self.assertEqual(len(deleted), 3)
        self.assertTrue(deleted[0].endswith("top/sub/b.txt"), "깊은 것부터 지워야 한다")

    def test_a_bucket_cannot_be_deleted_from_here(self):
        made, transport = link(lambda m, p, b: FakeResponse(200, b""))
        with self.assertRaises(s3.S3Error):
            made.remote_delete("bucket")
        self.assertEqual(transport.calls, [])

    def test_rename_copies_then_deletes_every_key(self):
        body = xml_listing(contents=[
            ("old/a.txt", 1, "2024-01-01T00:00:00Z"),
            ("old/sub/b.txt", 1, "2024-01-01T00:00:00Z"),
        ])
        made, transport = link(
            lambda m, p, b: FakeResponse(200, body if m == "GET" else b"")
        )
        made.remote_rename("bucket/old", "new")

        copies = [c for c in transport.calls if c["method"] == "PUT"]
        self.assertEqual(len(copies), 2)
        self.assertEqual(
            sorted(c["headers"]["x-amz-copy-source"] for c in copies),
            ["/bucket/old/a.txt", "/bucket/old/sub/b.txt"],
        )
        self.assertTrue(any(c["path"].endswith("new/sub/b.txt") for c in copies))
        self.assertEqual(len([c for c in transport.calls if c["method"] == "DELETE"]), 2)

    def test_rename_refuses_a_path_separator(self):
        made, transport = link(lambda m, p, b: FakeResponse(200, b""))
        with self.assertRaises(s3.S3Error):
            made.remote_rename("bucket/a.txt", "b/c.txt")
        self.assertEqual(transport.calls, [])


class TestErrors(unittest.TestCase):
    def test_an_s3_error_reads_as_a_session_error(self):
        """복사와 목록 경로가 이미 SessionError 를 잡는다. 그 길을 그대로 탄다."""
        self.assertTrue(issubclass(s3.S3Error, session.SessionError))

    def test_the_xml_error_body_becomes_the_message(self):
        body = (
            "<Error><Code>AccessDenied</Code>"
            "<Message>Access Denied</Message></Error>"
        ).encode("utf-8")
        made, _ = link(lambda m, p, b: FakeResponse(403, body))

        with self.assertRaises(s3.S3Error) as caught:
            made.remote_list("bucket")
        self.assertIn("AccessDenied", str(caught.exception))

    def test_a_wrong_region_says_which_region_to_use(self):
        made, _ = link(
            lambda m, p, b: FakeResponse(301, b"", {"x-amz-bucket-region": "us-west-2"})
        )
        with self.assertRaises(s3.S3Error) as caught:
            made.remote_list("bucket")
        self.assertIn("us-west-2", str(caught.exception))

    def test_a_network_failure_is_reported_not_raised_raw(self):
        def boom(*args, **kwargs):
            raise OSError("이름을 못 찾았다")

        made = s3.S3Link("ap-northeast-2", "k", "s", transport=boom,
                         clock=lambda: EXAMPLE_TIME)
        with self.assertRaises(s3.S3Error):
            made.remote_list("bucket")


class TestEndpoint(unittest.TestCase):
    def test_a_plain_bucket_uses_the_virtual_hosted_form(self):
        made, transport = link(lambda m, p, b: FakeResponse(200, xml_listing()))
        made.remote_list("mybucket")

        self.assertEqual(transport.calls[0]["host"], "mybucket.s3.ap-northeast-2.amazonaws.com")
        self.assertTrue(transport.calls[0]["path"].startswith("/?"))

    def test_a_dotted_bucket_falls_back_to_the_path_form(self):
        """`*.s3...` 와일드카드 인증서가 점 하나까지만 덮는다. TLS 검증이 깨진다."""
        made, transport = link(lambda m, p, b: FakeResponse(200, xml_listing()))
        made.remote_list("my.bucket.com")

        self.assertEqual(transport.calls[0]["host"], "s3.ap-northeast-2.amazonaws.com")
        self.assertTrue(transport.calls[0]["path"].startswith("/my.bucket.com"))


class TestProfileStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        target = Path(self.tmp.name) / "settings.json"
        original = settings._path
        settings._path = lambda: target
        self.addCleanup(lambda: setattr(settings, "_path", original))
        self.target = target

    def test_the_profile_survives_a_round_trip(self):
        settings.save_s3_profile("ap-northeast-2", EXAMPLE_KEY, "my-bucket")
        self.assertEqual(
            settings.load_s3_profile(),
            {"region": "ap-northeast-2", "access_key": EXAMPLE_KEY, "bucket": "my-bucket"},
        )

    def test_the_secret_key_is_never_written_to_disk(self):
        settings.save_s3_profile("ap-northeast-2", EXAMPLE_KEY, "my-bucket")
        self.assertNotIn(EXAMPLE_SECRET, self.target.read_text(encoding="utf-8"))

    def test_a_missing_profile_reads_as_empty_fields(self):
        self.assertEqual(
            settings.load_s3_profile(), {"region": "", "access_key": "", "bucket": ""}
        )

    def test_saving_the_profile_does_not_wipe_other_settings(self):
        settings.save_theme("blue")
        settings.save_s3_profile("ap-northeast-2", EXAMPLE_KEY, "")
        self.assertEqual(settings.load_theme(), "blue")


# --- 화면 쪽 ---


class S3AppTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / "note.txt").write_bytes(b"hello")

        self.app = app_module.App(
            my_name="s3-node",
            shares=make_shares(self.root),
            persist=False,
            tcp_port=0,
            udp_port=0,
            platform_name="testos",
            use_discovery=False,
        )
        self.addCleanup(self.app.shutdown)
        self.app.local.refresh()

    def settle(self):
        """워커 스레드가 끝나기를 기다렸다가 이벤트를 처리한다.

        목록은 UI 를 막지 않으려고 스레드에서 읽는다. 기다리지 않으면 테스트가
        가끔만 통과한다 - 그런 테스트는 없느니만 못하다.
        """
        for _ in range(10):
            busy = [
                t for t in threading.enumerate()
                if t.name.startswith(("list-", "s3-connect"))
            ]
            for thread in busy:
                thread.join(timeout=5)
            self.app._drain_events()
            if not busy:
                return

    def commands(self):
        return next(
            items for title, items in app_module.MenuBar(self.app).menus
            if title == "Commands"
        )

    def attach(self, bucket="my-bucket"):
        """가짜 S3 를 붙인다. 연결 이벤트가 도는 길을 그대로 지난다."""
        made, transport = link(
            lambda m, p, b: FakeResponse(200, xml_listing(
                contents=[("hello.txt", 12, "2024-05-06T07:08:09Z")],
                prefixes=["logs/"],
            )),
            bucket=bucket,
        )
        self.app.events.put(("s3_connected", made))
        self.settle()
        return made, transport


class TestCommandsMenu(S3AppTestCase):
    def test_the_menu_offers_an_s3_connection(self):
        labels = [label for label, _, action in self.commands() if action]
        self.assertIn("AWS S3 연결", labels)

    def test_choosing_it_opens_the_credentials_dialog(self):
        action = next(a for l, _, a in self.commands() if l == "AWS S3 연결")
        action()
        self.assertIsInstance(self.app.modal, app_module.S3ConnectDialog)

    def test_it_refuses_while_a_peer_is_connected(self):
        """오른쪽 패널은 하나다. 둘 다 붙으면 어느 쪽을 보는지 알 수 없다."""
        class Stub:
            peer_name = "옆자리PC"

            def close(self, reason=None):
                pass

        self.app.session = Stub()
        self.app._open_s3_dialog()

        self.assertIsNone(self.app.modal)
        self.assertIn("먼저 연결을 끊으세요", self.app.status)


class TestCredentialsDialog(S3AppTestCase):
    def open(self, profile=None):
        got = []
        dialog = app_module.S3ConnectDialog(got.append, profile)
        return dialog, got

    def type(self, dialog, text):
        for ch in text:
            dialog.key(ch)

    def test_the_saved_profile_prefills_every_field_but_the_secret(self):
        dialog, _ = self.open(
            {"region": "ap-northeast-2", "access_key": EXAMPLE_KEY, "bucket": "b"}
        )
        self.assertEqual(dialog.values["region"], "ap-northeast-2")
        self.assertEqual(dialog.values["access_key"], EXAMPLE_KEY)
        self.assertEqual(dialog.values["secret_key"], "")

    def test_tab_moves_between_fields_and_keeps_what_was_typed(self):
        dialog, _ = self.open()
        self.type(dialog, "seoul")
        dialog.key(term.TAB)
        self.type(dialog, "AKIA")

        dialog._store()
        self.assertEqual(dialog.values["region"], "seoul")
        self.assertEqual(dialog.values["access_key"], "AKIA")

    def test_the_secret_key_is_masked_on_screen(self):
        dialog, _ = self.open()
        for _ in range(2):
            dialog.key(term.TAB)
        self.type(dialog, "s3cr3t")

        text = "\n".join(dialog.lines(60))
        self.assertNotIn("s3cr3t", text)
        self.assertIn("*" * 6, text)

    def test_enter_with_an_empty_field_keeps_the_dialog_open(self):
        """닫아버리면 쳐 넣은 것이 다 사라진다. 무엇이 빠졌는지만 말한다."""
        dialog, got = self.open()
        self.type(dialog, "ap-northeast-2")

        self.assertFalse(dialog.key(term.ENTER))
        self.assertEqual(got, [])
        self.assertIn("액세스 키 ID", "\n".join(dialog.lines(60)))
        self.assertIn("비었습니다", dialog.error)

    def test_the_bucket_may_be_left_empty(self):
        dialog, got = self.open()
        self.type(dialog, "ap-northeast-2")
        dialog.key(term.TAB)
        self.type(dialog, EXAMPLE_KEY)
        dialog.key(term.TAB)
        self.type(dialog, "secret")

        self.assertTrue(dialog.key(term.ENTER))
        self.assertEqual(got[0]["bucket"], "")

    def test_escape_submits_nothing(self):
        dialog, got = self.open()
        self.type(dialog, "ap-northeast-2")
        self.assertTrue(dialog.key(term.ESC))
        self.assertEqual(got, [])


class TestConnectedPane(S3AppTestCase):
    def test_connecting_points_the_right_pane_at_the_bucket_list(self):
        self.attach()

        self.assertTrue(self.app.right.is_remote)
        self.assertEqual(self.app.right.path, shares_mod.ROOT)
        self.assertEqual([e["name"] for e in self.app.right.entries], ["my-bucket"])

    def test_the_pane_title_names_the_bucket(self):
        self.attach()
        self.assertIn("my-bucket", self.app.right.title)

    def test_the_header_shows_the_s3_connection(self):
        self.attach()
        self.assertIn("my-bucket", self.app._header(100))

    def test_the_pane_lists_folders_and_objects(self):
        self.attach()
        self.app.right.navigate("my-bucket")
        self.settle()

        names = sorted((e["name"], e["is_dir"]) for e in self.app.right.entries)
        self.assertEqual(names, [("hello.txt", False), ("logs", True)])

    def test_guard_messages_talk_about_buckets_not_shares(self):
        self.attach()
        self.app.focus = self.app.right
        self.app._ask_mkdir()
        self.assertIn("버킷", self.app.status)

    def test_disconnecting_clears_the_pane(self):
        self.attach()
        self.app._disconnect()

        self.assertIsNone(self.app.s3)
        self.assertIsNone(self.app.link)
        self.assertEqual(self.app.right.entries, [])
        self.assertIs(self.app.focus, self.app.local)

    def test_an_incoming_peer_connection_takes_the_pane_over(self):
        """승인한 쪽이 사용자다. 거절하지 않고 자리를 내주되 말은 해준다."""
        class Stub:
            peer_name = "옆자리PC"

            def remote_home(self):
                return shares_mod.ROOT

            def close(self, reason=None):
                pass

        self.attach()
        self.app.events.put(("connected", Stub()))
        self.app._drain_events()

        self.assertIsNone(self.app.s3)
        self.assertIs(self.app.link, self.app.session)
        self.assertIn("놓았습니다", self.app.status)


class TestConnectValidation(S3AppTestCase):
    """붙기 전에 목록을 한 번 읽어본다. 그래야 패널이 붙은 뒤에 실패하지 않는다."""

    def intercept(self, reply):
        """실제 HTTPS 자리를 가짜로 바꾼다. 테스트가 바깥으로 나가면 안 된다."""
        transport = FakeTransport(reply)
        original = s3.https
        s3.https = transport
        self.addCleanup(lambda: setattr(s3, "https", original))
        return transport

    def connect(self, bucket):
        self.app._connect_s3({
            "region": "ap-northeast-2", "access_key": "k",
            "secret_key": "s", "bucket": bucket,
        })
        self.settle()

    def test_a_rejected_key_never_becomes_a_connection(self):
        body = b"<Error><Code>InvalidAccessKeyId</Code><Message>bad</Message></Error>"
        self.intercept(lambda m, p, b: FakeResponse(403, body))
        self.connect("")

        self.assertIsNone(self.app.s3)
        self.assertIsInstance(self.app.modal, app_module.MessageDialog)

    def test_a_fixed_bucket_is_verified_too(self):
        """루트 목록은 그 버킷 하나를 그대로 돌려준다. 그것으로는 검증이 안 된다."""
        transport = self.intercept(lambda m, p, b: FakeResponse(200, xml_listing()))
        self.connect("my-bucket")

        self.assertIsNotNone(self.app.s3)
        self.assertTrue(transport.calls, "네트워크를 한 번도 지나지 않았다")

    def test_a_good_key_becomes_a_connection(self):
        self.intercept(lambda m, p, b: FakeResponse(200, xml_listing()))
        self.connect("")

        self.assertIsNotNone(self.app.s3)
        self.assertIs(self.app.link, self.app.s3)


class TestCopyThroughThePanes(S3AppTestCase):
    """F5 복사가 S3 를 상대로도 그대로 돈다.

    전송 로직은 손대지 않았다. `link` 자리에 S3 가 꽂히면 같은 코드가 push/pull 을
    S3 쪽으로 부른다 - 그것이 실제로 그런지 확인한다.
    """

    def confirm(self):
        """복사 확인 상자에서 y 를 누른다."""
        self.assertIsInstance(self.app.modal, app_module.ConfirmDialog)
        self.app.modal.key("y")
        self.app.modal = None

    def wait_for_copy(self):
        for _ in range(20):
            busy = [t for t in threading.enumerate() if t.name in ("copy", "list-remote")]
            for thread in busy:
                thread.join(timeout=10)
            self.app._drain_events()
            if not busy:
                return

    def test_upload_puts_the_local_file_into_the_bucket(self):
        made, transport = link(
            lambda m, p, b: FakeResponse(200, xml_listing() if m == "GET" else b""),
            bucket="my-bucket",
        )
        self.app.events.put(("s3_connected", made))
        self.settle()
        self.app.right.navigate("my-bucket/받는곳")
        self.settle()

        self.app.focus = self.app.left
        self.app.left.cursor = 1  # '..' 이 아닌 note.txt
        self.app._start_copy()
        self.confirm()
        self.wait_for_copy()

        puts = [c for c in transport.calls if c["method"] == "PUT"]
        self.assertTrue(puts, "올리는 요청이 나가지 않았다")
        self.assertTrue(puts[-1]["path"].endswith("/note.txt"), puts[-1]["path"])
        self.assertIn("%EB%B0%9B%EB%8A%94%EA%B3%B3", puts[-1]["path"])  # 받는곳
        self.assertEqual(puts[-1]["body"], b"hello")

    def test_download_writes_the_object_into_the_local_folder(self):
        listing = xml_listing(contents=[("받을.bin", 4, "2024-05-06T07:08:09Z")])
        made, transport = link(
            lambda m, p, b: FakeResponse(200, listing if b"list-type" in p.encode() else b"DATA"),
            bucket="my-bucket",
        )
        self.app.events.put(("s3_connected", made))
        self.settle()
        self.app.right.navigate("my-bucket")
        self.settle()
        self.app.left.navigate(self.root)

        self.app.focus = self.app.right
        self.app.right.cursor = 1  # '..' 이 아닌 받을.bin
        self.app._start_copy()
        self.confirm()
        self.wait_for_copy()

        self.assertEqual((self.root / "받을.bin").read_bytes(), b"DATA")

    def test_copying_needs_a_connection_first(self):
        self.app.focus = self.app.left
        self.app.left.cursor = 1
        self.app._start_copy()

        self.assertIsNone(self.app.modal)
        self.assertIn("먼저 연결하세요", self.app.status)


if __name__ == "__main__":
    unittest.main()
