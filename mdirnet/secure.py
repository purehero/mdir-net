# 전송 암호화 - TCP 위에 TLS 1.3 을 씌운다 (PROTOCOL.md 0.1절)
#
# 키를 **저장하지 않는다.** 프로세스가 켜질 때 만들고 끝나면 사라진다. 저장하지
# 않으므로 보호할 것도 없다 - 파일 권한도, 키스토어도, 갱신도 필요 없다. 앱을
# 뜯어도 얻을 것이 없다.
#
# 인증서는 목적이 아니라 TLS 가 공개키를 알아먹는 형식일 뿐이다. CA 도 신뢰
# 저장소도 없고, 상대 인증서를 검증하지 않는다.
#
# 막는 것과 못 막는 것을 분명히 해둔다.
#   막는다     같은 망에서의 패킷 캡처, 공유기의 트래픽 기록
#   못 막는다  능동적 중간자 (상대가 누구인지 확인할 수단이 없다)

import datetime
import os
import socket
import ssl
import tempfile

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

#: 인증서에 적을 이름. 검증하지 않으므로 아무 값이나 되지만 사람이 볼 때를 위해 둔다.
COMMON_NAME = "mdirnet-node"

#: 프로세스보다 오래 살 이유가 없다. 시계가 어긋난 기기를 위해 넉넉히 잡는다.
VALID_DAYS = 3650

#: 시계가 조금 앞선 기기에서 "아직 유효하지 않음" 이 나지 않게 뒤로 물린다.
BACKDATE_DAYS = 1


class Identity:
    """이 프로세스의 한 판짜리 신원. 디스크에 남지 않는다."""

    def __init__(self, key_pem, cert_pem, cert_der):
        self.key_pem = key_pem
        self.cert_pem = cert_pem
        self.cert_der = cert_der


def make_identity():
    """켜질 때마다 새로 만든다. 저장하지 않는다."""
    key = ec.generate_private_key(ec.SECP256R1())

    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, COMMON_NAME)])
    now = datetime.datetime.now(datetime.timezone.utc)

    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)  # 자기가 자기를 서명한다
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=BACKDATE_DAYS))
        .not_valid_after(now + datetime.timedelta(days=VALID_DAYS))
        .sign(key, hashes.SHA256())
    )

    return Identity(
        key_pem=key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        cert_pem=cert.public_bytes(serialization.Encoding.PEM),
        cert_der=cert.public_bytes(serialization.Encoding.DER),
    )


def server_context(identity):
    """받는 쪽. 인증서를 내밀고 상대에게는 요구하지 않는다.

    파이썬 `ssl` 은 **모르는 자기서명 클라이언트 인증서를 받아주지 못한다.**
    요구하려면 검증을 켜야 하는데 CA 가 없어 실패한다 (검증 콜백을 끼울 구멍도
    없다). 그래서 상호 인증은 하지 않는다.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.verify_mode = ssl.CERT_NONE
    _load_identity(ctx, identity)
    return ctx


def client_context():
    """거는 쪽. 상대 인증서를 검증하지 않는다 - 검증할 체인이 없다.

    `CERT_NONE` 이어도 `getpeercert(binary_form=True)` 로 상대 인증서는 얻을 수
    있다. 나중에 지문을 화면에 띄울 때 쓴다.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def wrap_server(sock, identity):
    return server_context(identity).wrap_socket(sock, server_side=True)


def wrap_client(sock):
    # 이름을 검증하지 않으므로 server_hostname 은 형식상의 값이다
    return client_context().wrap_socket(sock, server_hostname=COMMON_NAME)


def peer_certificate(tls_sock):
    """상대 인증서 DER. 못 받았으면 None."""
    try:
        return tls_sock.getpeercert(binary_form=True)
    except (ValueError, AttributeError):
        return None


def _load_identity(ctx, identity):
    """`load_cert_chain` 은 **파일만** 받는다. 메모리에서 바로 못 넣는다.

    그래서 0600 임시 파일에 잠깐 쓰고 곧바로 지운다. 디스크에 남는 시간은
    `load_cert_chain` 한 번이고, 파일 이름은 프로세스마다 다르다.
    """
    key_fd, key_path = tempfile.mkstemp(prefix="mdirnet-", suffix=".key")
    cert_fd, cert_path = tempfile.mkstemp(prefix="mdirnet-", suffix=".crt")
    try:
        os.chmod(key_path, 0o600)
        os.chmod(cert_path, 0o600)
        with os.fdopen(key_fd, "wb") as handle:
            handle.write(identity.key_pem)
        with os.fdopen(cert_fd, "wb") as handle:
            handle.write(identity.cert_pem)

        ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
    finally:
        for path in (key_path, cert_path):
            try:
                os.unlink(path)
            except OSError:
                pass


def close_quietly(sock):
    """TLS 소켓은 닫을 때도 상대에게 알림을 보내다 실패할 수 있다."""
    try:
        sock.close()
    except (OSError, ssl.SSLError, socket.timeout):
        pass
