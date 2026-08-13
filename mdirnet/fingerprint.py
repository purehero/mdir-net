# 기기 지문 - 인증서에서 사람이 대조할 짧은 값을 만든다 (PROTOCOL.md 2.5절)
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


def fingerprint(cert_der):
    """DER 인증서 하나에서 `A3F2-91C4-7B0E` 꼴의 기기 지문을 만든다.

    **인증서 하나만 넣는다.** 서버만 인증서를 내밀기 때문이다 (0.1절). 받는 쪽은
    상대 인증서를 갖지 못하므로 둘을 섞는 규칙은 애초에 쓸 수 없다.

    이 값은 SSH 호스트 키 지문과 같은 성격이다. 받는 쪽은 **자기 지문**을 띄우고,
    거는 쪽은 **받은 인증서의 지문**을 띄운다. 두 값이 같으면 중간에 아무도 없다.
    중간자는 상대의 개인키가 없어 그 인증서를 내밀 수 없기 때문이다.
    """
    if not cert_der:
        raise ValueError("인증서가 비어 있습니다")

    digest = hashlib.sha256(LABEL + hashlib.sha256(cert_der).digest()).digest()
    text = digest[:LENGTH].hex().upper()

    return "-".join(text[i : i + GROUP] for i in range(0, len(text), GROUP))
