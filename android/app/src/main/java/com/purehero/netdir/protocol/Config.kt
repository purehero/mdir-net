// 프로토콜 상수 — mdirnet/config.py 의 거울. PROTOCOL.md 1절이 기준이다

package com.purehero.netdir.protocol

object Config {
    const val PROTOCOL_VERSION = 2

    /** 세션 TCP. 사용 중이면 1 씩 올려 PORT_TRIES 번까지 시도한다 */
    const val TCP_PORT = 9600

    /** 탐색 브로드캐스트 UDP. 고정이다 */
    const val UDP_PORT = 9601

    /** 9600~9609 */
    const val PORT_TRIES = 10

    /** 자기 존재를 알리는 주기 */
    const val ANNOUNCE_INTERVAL_MS = 2_000L

    /** 이 시간 동안 소식이 없으면 피어 목록에서 지운다 */
    const val PEER_TTL_MS = 6_000L

    /** data 프레임 하나에 담는 파일 데이터 */
    const val CHUNK_SIZE = 256 * 1024

    /** head 응답의 상한 */
    const val MAX_VIEW_BYTES = 256 * 1024

    /** 깨졌거나 악의적인 상대가 메모리를 고갈시키지 못하게 막는다 */
    const val MAX_HEADER_BYTES = 1 * 1024 * 1024
    const val MAX_BLOB_BYTES = 4 * 1024 * 1024

    const val CONNECT_TIMEOUT_MS = 8_000
    const val APPROVAL_TIMEOUT_MS = 30_000L
    const val REQUEST_TIMEOUT_MS = 30_000L

    /**
     * 요청하는 쪽이 승인을 기다리는 시간. 상대가 29 초째에 승인 버튼을 눌렀는데
     * 이쪽이 이미 포기해 있으면 안 되므로 여유를 더 준다
     */
    const val APPROVAL_WAIT_MS = APPROVAL_TIMEOUT_MS + 5_000L

    /** 탐색 알림과 hello 에 실어보내는 값. 표시용일 뿐 동작에 영향은 없다 */
    const val PLATFORM = "android"
}
