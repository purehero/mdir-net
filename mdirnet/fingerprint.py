# 연결 지문 - 두 끝점의 인증서에서 사람이 대조할 짧은 값을 만든다 (PROTOCOL.md 2.5절)
#
# 암호화만으로는 중간자를 막지 못한다. 자기서명 인증서에는 검증할 체인이 없으므로,
# 양쪽 화면에 같은 값이 뜨는지 **사람이 보는 것**이 유일한 인증 수단이다.
#
# 상수를 config.py 에 두지 않는 이유. tests/test_protocol_doc.py 가 config 의
# 대문자 이름을 전부 훑어 문서의 **숫자** 값과 대조한다. 문자열 라벨이 끼면
# 그 테스트가 깨진다. 선 위 규칙이므로 문서화는 PROTOCOL.md 2.5절에서 한다.

import hashlib

#: 다른 용도의 해시와 섞이지 않게 앞에 붙인다. 규칙이 바뀌면 뒤 숫자를 올린다.
LABEL = b"mdirnet-fingerprint-v1"

#: 화면에 내보내는 바이트 수. 48비트.
LENGTH = 6

#: 읽기 좋게 끊는 단위.
GROUP = 4


def fingerprint(cert_a, cert_b):
    """두 DER 인증서에서 `A3F2-91C4-7B0E` 꼴의 지문을 만든다.

    **두 인증서를 함께 넣는다.** 한쪽만 쓰면 중간자가 양쪽에 각각 다른 채널을
    열어도 두 화면의 값이 같아질 수 있다.

    **두 해시를 바이트 사전순으로 정렬해 넣는다.** 그래야 누가 걸었든 양쪽이
    같은 값을 얻는다. 클라이언트와 서버를 구분할 필요가 없어진다.
    """
    if not cert_a or not cert_b:
        raise ValueError("인증서가 비어 있습니다")

    one = hashlib.sha256(cert_a).digest()
    two = hashlib.sha256(cert_b).digest()
    low, high = sorted((one, two))

    digest = hashlib.sha256(LABEL + low + high).digest()
    text = digest[:LENGTH].hex().upper()

    return "-".join(text[i : i + GROUP] for i in range(0, len(text), GROUP))
