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
            val a = row.getString("certA").hexToBytes()
            val b = row.getString("certB").hexToBytes()

            assertEquals(
                row.getString("note"),
                row.getString("fingerprint"),
                Fingerprint.of(a, b),
            )
        }
    }

    /**
     * 벡터가 실제로 위험한 입력을 담고 있는지 확인한다.
     *
     * 순한 것만 들어 있으면 통과해도 아무것도 보증하지 못한다. 특히 0x80 이상은
     * Kotlin 의 Byte 가 음수라 정렬이 뒤집히는 자리다.
     */
    @Test
    fun `벡터가 부호 있는 바이트와 순서 뒤집기를 담고 있다`() {
        val rows = golden()
        var hasHighByte = false
        var hasSwapped = false

        val seen = mutableMapOf<Pair<String, String>, String>()
        for (index in 0 until rows.length()) {
            val row = rows.getJSONObject(index)
            val a = row.getString("certA")
            val b = row.getString("certB")

            if (a.hexToBytes().any { it.toInt() and 0xFF >= 0x80 }) hasHighByte = true
            if (seen.containsKey(b to a)) hasSwapped = true
            seen[a to b] = row.getString("fingerprint")
        }

        assertTrue("0x80 이상 바이트가 든 벡터가 없다", hasHighByte)
        assertTrue("순서를 뒤집은 짝이 없다", hasSwapped)
    }

    @Test
    fun `순서를 바꿔도 같은 값이다`() {
        val a = byteArrayOf(1, 2, 3)
        val b = byteArrayOf(9, 9, 9)

        assertEquals(Fingerprint.of(a, b), Fingerprint.of(b, a))
    }

    @Test
    fun `한 바이트만 달라도 값이 완전히 달라진다`() {
        val a = byteArrayOf(1, 2, 3)

        assertNotEquals(
            Fingerprint.of(a, byteArrayOf(9, 9, 9)),
            Fingerprint.of(a, byteArrayOf(9, 9, 8)),
        )
    }

    @Test
    fun `빈 인증서는 거부한다`() {
        // 파이썬도 같은 자리에서 거부한다. 상대 인증서를 못 받은 채로 지문을
        // 만들어 보여주면 사용자는 그것을 확인된 값으로 믿는다
        for (pair in listOf(
            ByteArray(0) to byteArrayOf(1),
            byteArrayOf(1) to ByteArray(0),
            ByteArray(0) to ByteArray(0),
        )) {
            try {
                Fingerprint.of(pair.first, pair.second)
                fail("빈 인증서를 받아들였다")
            } catch (_: IllegalArgumentException) {
            }
        }
    }

    @Test
    fun `보기 좋은 꼴이다`() {
        val text = Fingerprint.of(byteArrayOf(1), byteArrayOf(2))

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
