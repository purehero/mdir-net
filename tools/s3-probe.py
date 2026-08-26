#!/usr/bin/env python3
# S3 연결이 왜 실패했는지 오간 것을 그대로 보여주는 도구
#
#   python3 tools/s3-probe.py --region ap-northeast-2 --bucket my-bucket
#   python3 tools/s3-probe.py --region ap-northeast-2            버킷 없이 키만 확인
#
# `mdirnet.s3` 의 서명과 전송을 **그대로** 쓴다. 여기서 요청을 다시 조립하면
# 앱이 실제로 보내는 것과 어긋나 진단이 되지 않는다. 앱이 한 문장으로 줄여
# 보여주는 자리에서, 이것은 요청 줄과 서명 헤더와 HTTP 상태와 응답 본문을
# 통째로 찍는다.
#
# 실제로 이 도구가 첫 연결 실패의 원인을 짚었다. 키 문제로 보였던 것이
# 파이썬에 CA 인증서 묶음이 없어 TLS 악수에서 막힌 것이었다.
#
# 키는 환경변수(AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)에서 읽거나 물어본다.
# 시크릿 키는 화면에 찍지 않는다 - 출력을 그대로 붙여 넣어도 안전하다.

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mdirnet import s3  # noqa: E402


def show(title):
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def probe(link, label, method, bucket, key="", query=None):
    """요청 하나를 보내고 오간 것을 전부 찍는다."""
    show(label)

    host = link._host(bucket)
    path = link._url_path(bucket, key)
    signed = s3.sign(method, host, path, query, {}, s3.EMPTY_SHA256,
                     link.access_key, link.secret_key, link.region, link._clock())

    query_text = s3._canonical_query(query)
    line = s3._quote_path(path) + ("?" + query_text if query_text else "")

    print("요청     %s https://%s%s" % (method, host, line))
    print("서명 헤더")
    for name in sorted(signed):
        value = signed[name]
        if name == "Authorization":
            # 서명값은 길다. 앞부분만 보면 형태를 확인하기에 충분하다
            value = value[:96] + "..."
        print("    %-24s %s" % (name, value))

    try:
        resp = s3.https(host, method, line, signed, None, 30.0)
    except OSError as exc:
        print()
        print("!! 소켓/DNS 단계에서 실패: %s: %s" % (type(exc).__name__, exc))
        return None

    body = resp.read() or b""
    resp.close()

    print()
    print("응답     HTTP %d" % resp.status)
    for name in sorted(resp.headers):
        if name.startswith(("x-amz", "content-type", "date", "location", "server")):
            print("    %-24s %s" % (name, resp.headers[name]))
    print()
    text = body.decode("utf-8", "replace")
    print("본문 (%d바이트)" % len(body))
    print(text[:2000] if text.strip() else "    (비어 있음)")
    return resp.status


def main():
    parser = argparse.ArgumentParser(description="S3 연결 진단")
    parser.add_argument("--region", required=True, help="예: ap-northeast-2")
    parser.add_argument("--bucket", default="", help="비우면 ListBuckets 만 해본다")
    args = parser.parse_args()

    access = os.environ.get("AWS_ACCESS_KEY_ID") or input("액세스 키 ID: ").strip()
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY") or getpass.getpass("시크릿 액세스 키: ")

    link = s3.S3Link(args.region, access, secret.strip(), args.bucket)

    print("리전     %s" % args.region)
    print("키 ID    %s" % access)
    print("버킷     %s" % (args.bucket or "(지정 안 함)"))
    print("호스트   %s" % link._host(args.bucket or ""))

    # TLS 부터 막히면 요청은 나가지도 못한다. 무엇을 믿고 있는지 먼저 보여준다
    context = s3.tls_context()
    print("CA 묶음  %d개" % context.cert_store_stats()["x509_ca"])
    if not s3.has_ca_bundle():
        print()
        print("!! 믿을 CA 인증서를 하나도 찾지 못했습니다. TLS 검증이 반드시 실패합니다.")
        print("   %s" % s3._tls_message("(아직 시도하지 않음)").replace("\n", "\n   "))

    # 1) 계정의 버킷 목록. 권한이 없으면 여기서 AccessDenied 가 난다
    probe(link, "1. ListBuckets", "GET", "")

    if not args.bucket:
        return

    # 2) 그 버킷의 첫 페이지. 앱이 연결할 때 실제로 부르는 것과 같다
    probe(link, "2. ListObjectsV2 (앱이 연결할 때 부르는 것)", "GET", args.bucket, "",
          {"list-type": "2", "max-keys": "5", "delimiter": "/"})

    # 3) 버킷이 어느 리전인지. 리전이 틀리면 여기서 드러난다
    probe(link, "3. GetBucketLocation (버킷의 실제 리전)", "GET", args.bucket, "",
          {"location": ""})


if __name__ == "__main__":
    main()
