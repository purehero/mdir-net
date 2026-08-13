// 연결 지문 차분 검증 — 파이썬 fingerprint.py 가 낸 값과 한 글자도 갈리면 안 된다
//
// 지문은 인증 수단이다. 두 구현이 다른 값을 내면 사용자는 "다르네" 를 중간자 공격이
// 아니라 버그로 배우게 되고, 그러면 경고 자체가 무의미해진다.
//
// 벡터는 tools/gen-golden.py 가 만든다.

package com.purehero.netdir.protocol

import org.json.JSONArray
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class FingerprintGoldenTest {

    @Test
    fun `파이썬과 똑같은 지문을 낸다`() {
        val rows = golden()
        assertTrue("벡터가 비었다", rows.length() > 0)

        for (index in 0 until rows.length()) {
            val row = rows.getJSONObject(index)
            assertEquals(
                row.getString("note"),
                row.getString("fingerprint"),
                Fingerprint.of(row.getString("cert").hexToBytes()),
            )
        }
    }

    /**
     * 벡터가 실제로 위험한 입력을 담고 있는지 확인한다.
     *
     * 순한 것만 들어 있으면 통과해도 아무것도 보증하지 못한다. DER 에는 0x80 이상이
     * 흔하고, Kotlin 의 Byte 는 그것을 음수로 다룬다.
     */
    @Test
    fun `벡터가 부호 있는 바이트를 담고 있다`() {
        val rows = golden()
        var hasHighByte = false

        for (index in 0 until rows.length()) {
            val cert = rows.getJSONObject(index).getString("cert").hexToBytes()
            if (cert.any { it.toInt() and 0xFF >= 0x80 }) hasHighByte = true
        }

        assertTrue("0x80 이상 바이트가 든 벡터가 없다", hasHighByte)
    }

    @Test
    fun `같은 인증서면 같은 값이다`() {
        // 받는 쪽의 '내 지문' 과 거는 쪽의 '상대 지문' 이 같아야 대조가 성립한다
        assertEquals(Fingerprint.of(byteArrayOf(1, 2, 3)), Fingerprint.of(byteArrayOf(1, 2, 3)))
    }

    @Test
    fun `한 바이트만 달라도 값이 완전히 달라진다`() {
        assertNotEquals(
            Fingerprint.of(byteArrayOf(9, 9, 9)),
            Fingerprint.of(byteArrayOf(9, 9, 8)),
        )
    }

    @Test
    fun `빈 인증서는 거부한다`() {
        // 파이썬도 같은 자리에서 거부한다. 인증서를 못 받은 채로 지문을 만들어
        // 보여주면 사용자는 그것을 확인된 값으로 믿는다
        try {
            Fingerprint.of(ByteArray(0))
            fail("빈 인증서를 받아들였다")
        } catch (_: IllegalArgumentException) {
        }
    }

    @Test
    fun `보기 좋은 꼴이다`() {
        val text = Fingerprint.of(byteArrayOf(1))

        assertTrue("형식이 다르다: $text", Regex("^[0-9A-F]{4}(-[0-9A-F]{4}){2}$").matches(text))
    }

    private fun String.hexToBytes(): ByteArray =
        ByteArray(length / 2) { substring(it * 2, it * 2 + 2).toInt(16).toByte() }

    private fun golden() = JSONArray(
        checkNotNull(javaClass.classLoader!!.getResourceAsStream("golden/fingerprints.json")) {
            "golden/fingerprints.json 이 없습니다. tools/gen-golden.py 를 실행하세요"
        }.use { it.readBytes().toString(Charsets.UTF_8) },
    )
}
