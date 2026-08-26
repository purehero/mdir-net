# AWS S3 를 표준 라이브러리만으로 다루는 모듈 (SigV4 서명 + REST 호출)
#
# 이 프로젝트는 인증서를 만드는 `cryptography` 하나 말고는 표준 라이브러리만
# 쓴다. boto3 를 더하지 않고 서명과 REST 호출을 직접 짜는 이유다. 필요한 것은
# hmac/hashlib(서명), http.client(전송), xml.etree(응답 파싱) 셋뿐이다.
#
# 바깥에서 보는 얼굴은 `session.Session` 과 같다. 원격 패널과 복사 경로가
# `remote_list` / `pull_file` 같은 이름만 알고 상대가 피어인지 S3 인지 모르게
# 하려는 것이다. 그래서 App 은 둘을 같은 자리에 꽂아 쓴다.

import datetime
import hashlib
import hmac
import http.client
import os
import ssl
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

from . import config, fsops, session
from . import shares as shares_mod

# 빈 본문의 SHA-256. GET/DELETE 처럼 보낼 것이 없는 요청이 모두 이 값을 쓴다
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

# 업로드는 본문 해시를 미리 구하려면 파일을 두 번 읽어야 한다. HTTPS 위에서는
# S3 가 이 표식을 받아주므로 한 번만 읽고 흘려보낸다
UNSIGNED_PAYLOAD = "UNSIGNED-PAYLOAD"

ALGORITHM = "AWS4-HMAC-SHA256"
SERVICE = "s3"

# 한 번에 받아오는 객체 수. S3 의 상한이 1000 이다
PAGE_SIZE = 1000

# 고를 수 있는 리전. 코드와 사람이 읽는 이름을 짝지어 둔다.
#
# 목록은 상용 리전만 담는다. 중국(`amazonaws.com.cn`)과 GovCloud 는 엔드포인트
# 형태가 달라 이 모듈이 만드는 호스트로는 닿지 않는다. 목록에 없는 리전은
# 입력창에 직접 쳐 넣을 수 있게 열어두었다 - 리전은 계속 늘어난다.
REGIONS = (
    ("af-south-1", "아프리카 (케이프타운)"),
    ("ap-east-1", "아시아 태평양 (홍콩)"),
    ("ap-northeast-1", "아시아 태평양 (도쿄)"),
    ("ap-northeast-2", "아시아 태평양 (서울)"),
    ("ap-northeast-3", "아시아 태평양 (오사카)"),
    ("ap-south-1", "아시아 태평양 (뭄바이)"),
    ("ap-south-2", "아시아 태평양 (하이데라바드)"),
    ("ap-southeast-1", "아시아 태평양 (싱가포르)"),
    ("ap-southeast-2", "아시아 태평양 (시드니)"),
    ("ap-southeast-3", "아시아 태평양 (자카르타)"),
    ("ap-southeast-4", "아시아 태평양 (멜버른)"),
    ("ca-central-1", "캐나다 (중부)"),
    ("ca-west-1", "캐나다 서부 (캘거리)"),
    ("eu-central-1", "유럽 (프랑크푸르트)"),
    ("eu-central-2", "유럽 (취리히)"),
    ("eu-north-1", "유럽 (스톡홀름)"),
    ("eu-south-1", "유럽 (밀라노)"),
    ("eu-south-2", "유럽 (스페인)"),
    ("eu-west-1", "유럽 (아일랜드)"),
    ("eu-west-2", "유럽 (런던)"),
    ("eu-west-3", "유럽 (파리)"),
    ("il-central-1", "이스라엘 (텔아비브)"),
    ("me-central-1", "중동 (UAE)"),
    ("me-south-1", "중동 (바레인)"),
    ("sa-east-1", "남아메리카 (상파울루)"),
    ("us-east-1", "미국 동부 (버지니아 북부)"),
    ("us-east-2", "미국 동부 (오하이오)"),
    ("us-west-1", "미국 서부 (캘리포니아 북부)"),
    ("us-west-2", "미국 서부 (오리건)"),
)


def region_index(code):
    """목록에서 이 리전이 몇 번째인가. 없으면 0 이다."""
    return next((i for i, (c, _) in enumerate(REGIONS) if c == code), 0)


# 단일 PUT 으로 올릴 수 있는 상한. 넘으면 멀티파트가 필요한데 이 모듈은 하지
# 않는다. 조용히 실패하지 않고 미리 막는다
MAX_PUT_BYTES = 5 * 1024 * 1024 * 1024


class S3Error(session.SessionError):
    """S3 요청이 실패했다.

    `SessionError` 를 물려받는다. 복사/삭제/목록 경로가 이미 `SessionError` 를
    잡아 화면에 옮기고 있어서, 이 하나를 상속하면 원격 패널의 오류 처리가
    고스란히 재사용된다. 상속을 끊으면 같은 except 절을 대여섯 군데 고쳐야 한다.
    """


# --- 경로 ---


def split(remote_path):
    """원격 경로를 (버킷, 키) 로 나눈다. 가상 루트는 ("", "") 다.

    경로 공간은 피어 쪽과 똑같이 `/` 로 이어붙인 문자열이다. 첫 조각이 버킷,
    나머지가 키다. `..` 처리는 `shares` 의 규칙을 그대로 빌려 쓴다 - 규칙이
    두 벌이 되면 한쪽만 고치는 사고가 난다.
    """
    text = shares_mod.normalize(remote_path)
    if text == shares_mod.ROOT:
        return "", ""
    bucket, _, key = text.partition("/")
    return bucket, key


def _prefix(key):
    """키를 '폴더 접두어' 로 만든다. 빈 키는 버킷 루트라 접두어가 없다."""
    return key + "/" if key else ""


# --- SigV4 서명 ---


def _hmac(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def signing_key(secret, stamp, region):
    """날짜/리전/서비스를 차례로 태워 만든 서명 키.

    비밀 키를 그대로 쓰지 않고 하루치/리전별 키를 파생시키는 것이 SigV4 의
    핵심이다. 유출돼도 그날 그 리전 밖으로는 쓸 수 없다.
    """
    return _hmac(_hmac(_hmac(_hmac(("AWS4" + secret).encode("utf-8"), stamp), region), SERVICE),
                 "aws4_request")


def _quote_path(path):
    """경로를 URI 로 인코딩한다. `/` 는 조각 구분자라 남긴다.

    S3 는 경로를 두 번 인코딩하지 않는다 (다른 AWS 서비스와 다른 점이다).
    그래서 여기서 한 번 인코딩한 결과를 정규 요청과 실제 요청 줄에 똑같이 쓴다.
    """
    return urllib.parse.quote(path, safe="/~")


def _canonical_query(query):
    """정규 질의 문자열. 키로 정렬하고 값까지 인코딩한다."""
    if not query:
        return ""
    items = sorted((str(k), "" if v is None else str(v)) for k, v in query.items())
    return "&".join(
        "%s=%s" % (urllib.parse.quote(k, safe="~"), urllib.parse.quote(v, safe="~"))
        for k, v in items
    )


def sign(method, host, path, query, headers, payload_hash, access_key, secret_key,
         region, now):
    """요청에 서명해 `Authorization` 이 들어간 헤더 묶음을 돌려준다.

    `headers` 는 서명 대상에 포함될 헤더들이다. host / x-amz-date /
    x-amz-content-sha256 은 여기서 채운다.
    """
    stamp = now.strftime("%Y%m%d")
    moment = now.strftime("%Y%m%dT%H%M%SZ")

    full = dict(headers or {})
    full["host"] = host
    full["x-amz-date"] = moment
    full["x-amz-content-sha256"] = payload_hash

    # 정규 헤더는 이름을 소문자로 내리고 값의 앞뒤 공백을 없앤 뒤 이름순으로 늘어놓는다
    lowered = sorted((k.lower(), str(v).strip()) for k, v in full.items())
    canonical_headers = "".join("%s:%s\n" % (k, v) for k, v in lowered)
    signed_headers = ";".join(k for k, _ in lowered)

    canonical_request = "\n".join([
        method,
        _quote_path(path),
        _canonical_query(query),
        canonical_headers,
        signed_headers,
        payload_hash,
    ])

    scope = "%s/%s/%s/aws4_request" % (stamp, region, SERVICE)
    to_sign = "\n".join([
        ALGORITHM,
        moment,
        scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])

    signature = hmac.new(
        signing_key(secret_key, stamp, region), to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    full["Authorization"] = "%s Credential=%s/%s, SignedHeaders=%s, Signature=%s" % (
        ALGORITHM, access_key, scope, signed_headers, signature
    )
    return full


# --- 응답 파싱 ---


def _local_name(tag):
    """`{네임스페이스}Contents` 에서 `Contents` 만 뽑는다."""
    return tag.rsplit("}", 1)[-1]


def _find(node, name):
    for child in node:
        if _local_name(child.tag) == name:
            return child
    return None


def _text(node, name, default=""):
    child = _find(node, name)
    return child.text if child is not None and child.text is not None else default


def _iter(node, name):
    return [child for child in node if _local_name(child.tag) == name]


def _epoch(iso):
    """S3 의 ISO8601 시각을 epoch 초로. 못 읽으면 0.0 이다.

    화면의 날짜 칸에 쓸 뿐이라 실패해도 목록 전체를 버릴 이유가 없다.
    """
    text = (iso or "").strip()
    for form in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            moment = datetime.datetime.strptime(text, form)
        except ValueError:
            continue
        return moment.replace(tzinfo=datetime.timezone.utc).timestamp()
    return 0.0


def has_ca_bundle():
    """기본 SSL 컨텍스트가 신뢰할 CA 를 하나라도 들고 있는가.

    python.org 판 macOS 파이썬은 설치 직후 이 묶음이 비어 있다. 그 상태로는
    어떤 HTTPS 도 검증에 실패한다 - 피어 연결은 자기서명이라 검증을 하지
    않으므로, S3 가 이 프로젝트에서 CA 묶음을 처음 필요로 하는 곳이다.
    """
    try:
        return ssl.create_default_context().cert_store_stats()["x509_ca"] > 0
    except Exception:  # noqa: BLE001
        return True  # 모르면 안내를 붙이지 않는다. 틀린 안내가 없느니만 못하다


def _tls_message(exc):
    """인증서 검증 실패를 사람이 고칠 수 있는 문장으로 바꾼다.

    가장 흔한 원인은 CA 묶음이 없는 것이다. **그때만** 그 안내를 붙인다 -
    이름이 안 맞거나 만료된 인증서는 전혀 다른 이야기이고, 중간자를 만난
    것일 수도 있다.
    """
    lines = ["TLS 인증서를 확인하지 못했습니다."]
    if not has_ca_bundle():
        lines += [
            "이 파이썬에는 CA 인증서 묶음이 없습니다.",
            "python.org 판 macOS 파이썬은 설치 후 한 번 실행해야 합니다:",
            "    /Applications/Python 3.x/Install Certificates.command",
        ]
    lines.append("원래 오류: %s" % exc)
    return "\n".join(lines)


def _error_message(status, body):
    """S3 가 XML 로 돌려준 오류를 사람이 읽을 문장으로 바꾼다."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return "HTTP %d" % status

    code = _text(root, "Code") or "HTTP %d" % status
    message = _text(root, "Message")
    return "%s - %s" % (code, message) if message else code


# --- 전송 계층 ---


class Response:
    """응답 하나. 본문을 다 읽거나 close 할 때까지 연결을 붙들고 있다."""

    def __init__(self, conn, raw):
        self._conn = conn
        self._raw = raw
        self.status = raw.status
        self.headers = {k.lower(): v for k, v in raw.getheaders()}

    def read(self, size=None):
        return self._raw.read() if size is None else self._raw.read(size)

    def close(self):
        try:
            self._raw.close()
        finally:
            self._conn.close()


def https(host, method, path, headers, body, timeout):
    """기본 전송 계층. HTTPS 로 한 번 요청하고 `Response` 를 돌려준다.

    테스트는 이 자리에 가짜를 꽂는다. 그래서 S3Link 는 실제 통신 없이도 목록과
    전송 흐름을 통째로 확인할 수 있다.
    """
    conn = http.client.HTTPSConnection(host, timeout=timeout, context=ssl.create_default_context())
    try:
        conn.request(method, path, body=body, headers=headers)
        return Response(conn, conn.getresponse())
    except BaseException:
        conn.close()
        raise


class _Counting:
    """올리는 동안 읽은 바이트를 세어 진행률로 넘기는 껍데기.

    http.client 가 파일 객체를 블록 단위로 읽어 보내므로, read 를 가로채면
    업로드 진행률이 공짜로 나온다.
    """

    def __init__(self, fp, on_progress):
        self._fp = fp
        self._on_progress = on_progress

    def read(self, size=-1):
        chunk = self._fp.read(size)
        if chunk and self._on_progress:
            self._on_progress(len(chunk))
        return chunk


# --- 연결 ---


class S3Link:
    """S3 한 곳으로의 연결. `session.Session` 과 같은 이름들을 내놓는다.

    상태를 들고 있지 않다. 요청마다 새 HTTPS 연결을 열고 닫으므로 '연결' 은
    자격증명을 쥐고 있다는 뜻일 뿐이다. 끊기도 그래서 값이 없다.
    """

    def __init__(self, region, access_key, secret_key, bucket="", transport=None,
                 clock=None):
        self.region = region
        self.access_key = access_key
        self.secret_key = secret_key
        # 버킷을 정해두면 가상 루트가 그 하나만 보여준다. ListBuckets 권한이
        # 없는 키(버킷 하나로 제한된 키)가 흔해서, 루트를 열자마자 막히지 않게 한다
        self.bucket = bucket
        # 주지 않으면 실제 HTTPS 로 나간다. 부를 때 고르므로 테스트가 모듈의
        # `https` 를 갈아끼우는 것만으로 통째로 가로챌 수 있다
        self._transport = transport
        self._clock = clock or (lambda: datetime.datetime.now(datetime.timezone.utc))

    # --- 패널이 보는 이름 ---

    @property
    def peer_name(self):
        return "S3 %s" % self.bucket if self.bucket else "S3 %s" % self.region

    def close(self, reason=None):
        """연결이라 할 것이 없어 할 일도 없다. 세션과 얼굴을 맞추려고 둔다."""

    def closed(self):
        return False

    # --- 요청 ---

    def _host(self, bucket):
        """요청을 보낼 호스트.

        버킷 이름이 붙은 가상 호스트 방식을 쓰되, 이름에 점이 있으면 와일드카드
        인증서(`*.s3.리전.amazonaws.com`)가 한 단계밖에 못 덮어 TLS 검증이
        깨진다. 그때만 경로 방식으로 물러선다.
        """
        if bucket and "." not in bucket:
            return "%s.s3.%s.amazonaws.com" % (bucket, self.region)
        return "s3.%s.amazonaws.com" % self.region

    def _url_path(self, bucket, key):
        if bucket and "." not in bucket:
            return "/" + key
        return "/" + bucket + ("/" + key if key else "") if bucket else "/"

    def _call(self, method, bucket, key="", query=None, headers=None, body=None,
              payload_hash=EMPTY_SHA256, timeout=None):
        """서명해서 한 번 보낸다. 2xx 가 아니면 `S3Error` 를 던진다.

        본문을 부르는 쪽이 흘려 읽어야 하는 경우가 있어 응답을 그대로 돌려준다.
        닫는 책임도 부르는 쪽에 있다.
        """
        host = self._host(bucket)
        path = self._url_path(bucket, key)
        signed = sign(
            method, host, path, query, headers or {}, payload_hash,
            self.access_key, self.secret_key, self.region, self._clock(),
        )

        query_text = _canonical_query(query)
        line = _quote_path(path) + ("?" + query_text if query_text else "")

        try:
            resp = (self._transport or https)(
                host, method, line, signed, body, timeout or config.REQUEST_TIMEOUT
            )
        except ssl.SSLCertVerificationError as exc:
            # SSLError 는 OSError 라서 아래 절에 삼켜진다. 원인이 전혀 다르고
            # 고치는 방법도 달라 먼저 갈라낸다
            raise S3Error(_tls_message(exc))
        except OSError as exc:
            raise S3Error("S3 에 닿지 못했습니다: %s" % exc)

        if 200 <= resp.status < 300:
            return resp

        # 리전이 틀리면 S3 가 맞는 리전을 헤더에 실어준다. 그대로 알려주면
        # 사용자가 무엇을 고쳐야 하는지 바로 안다
        hint = resp.headers.get("x-amz-bucket-region")
        body_text = resp.read() or b""
        resp.close()
        if hint and hint != self.region:
            raise S3Error("리전이 다릅니다. %s 로 다시 연결하세요" % hint)
        raise S3Error(_error_message(resp.status, body_text))

    def _xml(self, method, bucket, key="", query=None):
        """응답 본문을 XML 로 파싱해 돌려준다."""
        resp = self._call(method, bucket, key, query)
        try:
            raw = resp.read()
        finally:
            resp.close()
        try:
            return ET.fromstring(raw)
        except ET.ParseError as exc:
            raise S3Error("응답을 읽지 못했습니다: %s" % exc)

    # --- 목록 ---

    def list_buckets(self):
        """계정의 버킷 목록. 버킷을 지정해 연결했으면 그 하나만 내놓는다."""
        if self.bucket:
            return [{"name": self.bucket, "is_dir": True, "size": 0, "mtime": 0.0}]

        root = self._xml("GET", "")
        holder = _find(root, "Buckets")
        if holder is None:
            return []
        return [
            {
                "name": _text(node, "Name"),
                "is_dir": True,
                "size": 0,
                "mtime": _epoch(_text(node, "CreationDate")),
            }
            for node in _iter(holder, "Bucket")
            if _text(node, "Name")
        ]

    def _pages(self, bucket, prefix, delimiter):
        """ListObjectsV2 를 끝까지 넘기며 페이지를 하나씩 내놓는다."""
        token = None
        while True:
            query = {"list-type": "2", "max-keys": str(PAGE_SIZE)}
            if prefix:
                query["prefix"] = prefix
            if delimiter:
                query["delimiter"] = delimiter
            if token:
                query["continuation-token"] = token

            root = self._xml("GET", bucket, "", query)
            yield root

            if _text(root, "IsTruncated").lower() != "true":
                return
            token = _text(root, "NextContinuationToken")
            if not token:
                return

    def remote_list(self, path):
        """한 폴더의 목록. (정규화된 경로, 항목 목록)."""
        bucket, key = split(path)
        if not bucket:
            return shares_mod.ROOT, self.list_buckets()

        prefix = _prefix(key)
        entries = []
        for page in self._pages(bucket, prefix, "/"):
            for node in _iter(page, "CommonPrefixes"):
                name = _text(node, "Prefix")[len(prefix):].rstrip("/")
                if name:
                    entries.append({"name": name, "is_dir": True, "size": 0, "mtime": 0.0})

            for node in _iter(page, "Contents"):
                full = _text(node, "Key")
                name = full[len(prefix):]
                # 폴더 자리를 나타내려고 만든 빈 키다. 목록에는 이미
                # CommonPrefixes 로 나오므로 이름 없는 항목이 되어버린다
                if not name or name.endswith("/"):
                    continue
                entries.append({
                    "name": name,
                    "is_dir": False,
                    "size": int(_text(node, "Size", "0") or 0),
                    "mtime": _epoch(_text(node, "LastModified")),
                })

        return shares_mod.normalize(path), entries

    def remote_tree(self, path):
        """(파일 목록, 하위 폴더 목록). 빈 폴더까지 옮기려고 둘 다 모은다."""
        bucket, key = split(path)
        if not bucket:
            raise S3Error("버킷 목록은 통째로 옮길 수 없습니다")

        prefix = _prefix(key)
        files, dirs = [], set()
        for page in self._pages(bucket, prefix, ""):
            for node in _iter(page, "Contents"):
                rel = _text(node, "Key")[len(prefix):]
                if not rel:
                    continue
                if rel.endswith("/"):
                    dirs.add(rel.rstrip("/"))  # 빈 폴더 자리표
                    continue
                files.append({"path": rel, "size": int(_text(node, "Size", "0") or 0)})
                # 중간 폴더도 적어둔다. 목적지가 미리 만들어 두면 되는 것이라
                # 겹쳐도 문제가 없다
                parts = rel.split("/")[:-1]
                for depth in range(1, len(parts) + 1):
                    dirs.add("/".join(parts[:depth]))

        return files, sorted(d for d in dirs if d)

    def remote_stat(self, path):
        bucket, key = split(path)
        if not key:
            return {"is_dir": True, "size": 0}
        resp = self._call("HEAD", bucket, key)
        try:
            return {"is_dir": False, "size": int(resp.headers.get("content-length", 0) or 0)}
        finally:
            resp.close()

    def remote_head(self, path, limit):
        """뷰어용. 파일 앞부분만 받아온다. (내용, 전체 크기)."""
        bucket, key = split(path)
        if not key:
            raise S3Error("폴더는 볼 수 없습니다")

        resp = self._call("GET", bucket, key, headers={"range": "bytes=0-%d" % (limit - 1)})
        try:
            data = resp.read()
            # 부분 응답이면 `bytes 0-9/1234` 꼴로 전체 크기가 온다. 통째로 왔다면
            # 받은 길이가 곧 전체다
            span = resp.headers.get("content-range", "")
            total = int(span.rsplit("/", 1)[-1]) if "/" in span else len(data)
        finally:
            resp.close()
        return data, total

    # --- 바꾸기 ---

    def remote_mkdir(self, path):
        """폴더 자리표를 만든다.

        S3 에 폴더는 없다. 이름이 `/` 로 끝나는 빈 객체를 두는 것이 콘솔과
        도구들이 함께 쓰는 관례다. 이것이 없으면 빈 폴더는 존재할 수 없다.
        """
        bucket, key = split(path)
        if not bucket:
            raise S3Error("버킷은 여기서 만들 수 없습니다")
        if not key:
            raise S3Error("버킷 자체를 만들지는 않습니다")

        resp = self._call("PUT", bucket, key + "/", headers={"content-length": "0"}, body=b"")
        resp.read()
        resp.close()

    def _keys_under(self, bucket, key):
        """이 경로가 가리키는 모든 키. 파일 하나면 그 하나다."""
        prefix = _prefix(key)
        found = []
        for page in self._pages(bucket, prefix, ""):
            found += [_text(node, "Key") for node in _iter(page, "Contents")]
        return [k for k in found if k]

    def remote_delete(self, path):
        """파일이면 그 키를, 폴더면 그 아래 전부를 지운다."""
        bucket, key = split(path)
        if not bucket:
            raise S3Error("버킷 목록에서는 지울 수 없습니다")
        if not key:
            raise S3Error("버킷은 지울 수 없습니다. AWS 콘솔에서 하세요")

        targets = self._keys_under(bucket, key) or [key]
        # 깊은 것부터 지운다. 자리표가 먼저 사라져 중간이 비어 보이지 않는다
        for target in sorted(targets, reverse=True):
            resp = self._call("DELETE", bucket, target)
            resp.read()
            resp.close()

    def remote_rename(self, path, new_name):
        """S3 에는 이름 바꾸기가 없다. 복사한 뒤 원본을 지운다.

        폴더면 그 아래 키를 하나씩 옮긴다. 큰 폴더에서는 오래 걸린다 - 그래도
        조용히 실패하는 것보다는 낫다.
        """
        bucket, key = split(path)
        if not bucket or not key:
            raise S3Error("여기서는 이름을 바꿀 수 없습니다")
        if "/" in new_name or "\\" in new_name:
            raise S3Error("경로 구분자는 쓸 수 없습니다")

        head = key.rsplit("/", 1)[0] if "/" in key else ""
        new_key = (head + "/" + new_name) if head else new_name

        targets = self._keys_under(bucket, key) or [key]
        for source in targets:
            # 파일 하나면 키 그대로, 폴더면 접두어만 갈아끼운다
            tail = source[len(key):]
            self._copy_key(bucket, source, new_key + tail)

        for source in sorted(targets, reverse=True):
            resp = self._call("DELETE", bucket, source)
            resp.read()
            resp.close()

    def _copy_key(self, bucket, source, target):
        resp = self._call(
            "PUT", bucket, target,
            headers={
                "x-amz-copy-source": _quote_path("/%s/%s" % (bucket, source)),
                "content-length": "0",
            },
            body=b"",
        )
        resp.read()
        resp.close()

    # --- 전송 ---

    def pull_file(self, remote_path, dest, on_progress=None):
        """S3 객체 하나를 내 dest 로 받는다. `.part` 로 받고 끝나면 바꿔친다."""
        bucket, key = split(remote_path)
        if not key:
            raise S3Error("폴더는 내려받을 수 없습니다")

        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = fsops.unique_part_path(dest)

        resp = self._call("GET", bucket, key, timeout=None)
        received = 0
        try:
            with open(part, "wb") as fp:
                while True:
                    chunk = resp.read(config.CHUNK_SIZE)
                    if not chunk:
                        break
                    fp.write(chunk)
                    received += len(chunk)
                    if on_progress:
                        on_progress(len(chunk))
            os.replace(part, dest)
            return received
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        finally:
            resp.close()

    def push_file(self, local_path, remote_path, on_progress=None):
        """내 파일 하나를 S3 로 올린다. 한 번의 PUT 으로 끝낸다."""
        bucket, key = split(remote_path)
        if not bucket:
            raise S3Error("버킷 목록에는 올릴 수 없습니다")
        if not key:
            raise S3Error("버킷 안의 이름이 필요합니다")

        local_path = Path(local_path)
        size = local_path.stat().st_size
        if size > MAX_PUT_BYTES:
            # 멀티파트 업로드는 하지 않는다. 반쯤 올리고 실패하는 것보다
            # 왜 안 되는지 먼저 말하는 편이 낫다
            raise S3Error("5GB 가 넘는 파일은 올릴 수 없습니다: %s" % fsops.human_size(size))

        with open(local_path, "rb") as fp:
            resp = self._call(
                "PUT", bucket, key,
                headers={"content-length": str(size)},
                body=_Counting(fp, on_progress) if size else b"",
                payload_hash=UNSIGNED_PAYLOAD,
                timeout=None,
            )
            resp.read()
            resp.close()
        return size
