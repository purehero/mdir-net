// 연결 지문 — mdirnet/fingerprint.py 의 거울 (PROTOCOL.md 2.5절)
//
// 암호화만으로는 중간자를 막지 못한다. 자기서명 인증서에는 검증할 체인이 없으므로,
// 양쪽 화면에 같은 값이 뜨는지 **사람이 보는 것**이 유일한 인증 수단이다.
//
// 안드로이드 클래스를 쓰지 않는다. 그래야 JVM 단위 테스트에서 파이썬이 만든
// 골든 벡터와 그대로 대조된다.

package com.purehero.netdir.protocol

import java.security.MessageDigest

object Fingerprint {

    /** 다른 용도의 해시와 섞이지 않게 앞에 붙인다. 규칙이 바뀌면 뒤 숫자를 올린다. */
    private val LABEL = "mdirnet-fingerprint-v1".toByteArray(Charsets.US_ASCII)

    /** 화면에 내보내는 바이트 수. 48비트. */
    const val LENGTH = 6

    /** 읽기 좋게 끊는 단위. */
    const val GROUP = 4

    /**
     * 두 DER 인증서에서 `A3F2-91C4-7B0E` 꼴의 지문을 만든다.
     *
     * **두 인증서를 함께 넣는다.** 한쪽만 쓰면 중간자가 양쪽에 각각 다른 채널을
     * 열어도 두 화면의 값이 같아질 수 있다.
     *
     * **두 해시를 바이트 사전순으로 정렬해 넣는다.** 그래야 누가 걸었든 양쪽이
     * 같은 값을 얻는다. 클라이언트와 서버를 구분할 필요가 없어진다.
     */
    fun of(certA: ByteArray, certB: ByteArray): String {
        require(certA.isNotEmpty() && certB.isNotEmpty()) { "인증서가 비어 있습니다" }

        val one = sha256(certA)
        val two = sha256(certB)

        // 파이썬의 `sorted()` 는 바이트를 **부호 없이** 비교한다. Kotlin 의 Byte 는
        // 부호가 있어서 그냥 비교하면 0x80 이상이 음수가 되어 순서가 뒤집힌다
        val ascending = compare(one, two) <= 0
        val low = if (ascending) one else two
        val high = if (ascending) two else one

        val digest = MessageDigest.getInstance("SHA-256").apply {
            update(LABEL)
            update(low)
            update(high)
        }.digest()

        val text = buildString {
            for (i in 0 until LENGTH) append("%02X".format(digest[i]))
        }

        return text.chunked(GROUP).joinToString("-")
    }

    private fun sha256(bytes: ByteArray): ByteArray =
        MessageDigest.getInstance("SHA-256").digest(bytes)

    /** 부호 없는 사전순 비교. 길이는 늘 32로 같지만 규칙을 분명히 둔다. */
    private fun compare(a: ByteArray, b: ByteArray): Int {
        for (i in 0 until minOf(a.size, b.size)) {
            val diff = (a[i].toInt() and 0xFF) - (b[i].toInt() and 0xFF)
            if (diff != 0) return diff
        }
        return a.size - b.size
    }
}
