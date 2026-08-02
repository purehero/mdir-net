// 프레이밍 계층 — 파이썬이 만든 골든 바이트를 정확히 읽는지, 규격 위반을 거부하는지

package net.mdir.android.protocol

import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.security.MessageDigest
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class FrameTest {

    // --- 골든 벡터 (파이썬 → Kotlin) ---

    /**
     * 프레임 11 개가 **이어붙어** 있는 스트림을 순서대로 읽어낸다.
     *
     * 하나씩 따로 두면 프레임 경계를 넘는 버퍼 처리를 못 잡는다. 프레이밍 버그는
     * 대개 거기에 산다.
     */
    @Test
    fun `파이썬이 만든 프레임을 그대로 읽는다`() {
        val expected = goldenManifest()
        val reader = FrameReader(ByteArrayInputStream(goldenBytes()))
        val produced = ByteArrayOutputStream()

        for (index in 0 until expected.length()) {
            val case = expected.getJSONObject(index)
            val note = case.getString("note")
            val frame = reader.read()

            assertSameJson("[$index] $note", case.getJSONObject("header"), frame.header)
            assertTrue(
                "[$index] $note — 리더가 blob 키를 헤더에서 없애야 한다",
                !frame.header.has("blob"),
            )

            val blobLen = case.getInt("blobLen")
            if (blobLen == 0) {
                // 빈 blob 도 뒤에 바이트가 없으므로 null 이다
                assertNull("[$index] $note — blob 이 없어야 한다", frame.blob)
            } else {
                assertEquals("[$index] $note — blob 길이", blobLen, frame.blob!!.size)
                assertEquals(
                    "[$index] $note — blob 내용",
                    case.getString("blobSha256"),
                    sha256(frame.blob!!),
                )
            }

            // 되돌려 쓴 것을 파이썬 쪽 테스트가 읽는다 (Kotlin → 파이썬 방향 검증)
            produced.write(Frame.encode(frame.header, frame.blob))
        }

        assertEndOfStream(reader)
        emitForPython(produced.toByteArray(), expected.length())
    }

    /** 스트림을 다 읽은 뒤 한 번 더 읽으면 종료로 알려야 한다. */
    private fun assertEndOfStream(reader: FrameReader) {
        try {
            reader.read()
            fail("프레임이 더 남아있다")
        } catch (_: PeerClosed) {
        }
    }

    // --- 왕복 ---

    @Test
    fun `직접 만든 프레임을 스스로 다시 읽는다`() {
        val header = JSONObject().put("t", "data").put("id", 7).put("q", 1)
        val blob = ByteArray(Config.CHUNK_SIZE) { (it % 251).toByte() }

        val frame = FrameReader(ByteArrayInputStream(Frame.encode(header, blob))).read()

        assertEquals("data", frame.header.getString("t"))
        assertEquals(7, frame.header.getInt("id"))
        assertEquals(sha256(blob), sha256(frame.blob!!))
    }

    /**
     * 빈 blob 은 헤더에 `blob:0` 을 남기지만 뒤에 바이트를 쓰지는 않는다.
     *
     * 골든 벡터의 왕복으로는 이 경로가 검사되지 않는다. 리더가 `blob:0` 을 null 로
     * 돌려주므로 되돌려 쓸 때는 blob 키가 아예 빠지기 때문이다. 파이썬의
     * `encode(h, b"")` 와 같은지 직접 확인한다.
     */
    @Test
    fun `빈 blob 은 헤더에만 남고 바이트는 쓰지 않는다`() {
        val header = JSONObject().put("t", "data").put("id", 3)
        val frame = Frame.encode(header, ByteArray(0))

        val headerText = String(frame, 4, frame.size - 4, Charsets.UTF_8)
        assertEquals("blob 바이트가 붙었다", 0, JSONObject(headerText).getInt("blob"))
        assertEquals("길이 접두어와 헤더 말고는 아무것도 없어야 한다", 4 + headerText.length, frame.size)

        // 읽는 쪽은 파이썬과 마찬가지로 blob 없음으로 본다
        assertNull(FrameReader(ByteArrayInputStream(frame)).read().blob)
    }

    @Test
    fun `헤더 객체를 건드리지 않는다`() {
        // 파이썬은 dict(header, blob=..) 로 복사본을 만든다. 원본에 blob 이 남으면
        // 같은 헤더를 다시 보낼 때 길이가 어긋난 프레임이 나간다
        val header = JSONObject().put("t", "data").put("id", 1)
        Frame.encode(header, ByteArray(10))
        assertTrue("호출한 쪽 헤더에 blob 이 남았다", !header.has("blob"))
    }

    // --- 규격 위반은 거부한다 ---

    @Test
    fun `헤더 길이 상한을 넘으면 거부한다`() {
        assertProtocolError(lengthPrefix(Config.MAX_HEADER_BYTES + 1))
    }

    @Test
    fun `길이의 최상위 비트가 켜져도 음수로 보지 않는다`() {
        // Int 로 그대로 담으면 -1 이 되어 상한 검사를 그냥 통과한다
        assertProtocolError(byteArrayOf(-1, -1, -1, -1))
    }

    @Test
    fun `blob 길이 상한을 넘으면 거부한다`() {
        val header = JSONObject().put("t", "data").put("blob", Config.MAX_BLOB_BYTES + 1)
        assertProtocolError(rawFrame(header.toString()))
    }

    @Test
    fun `blob 길이가 음수면 거부한다`() {
        assertProtocolError(rawFrame(JSONObject().put("t", "data").put("blob", -1).toString()))
    }

    @Test
    fun `blob 길이가 정수가 아니면 거부한다`() {
        // optInt 로 읽으면 0 으로 조용히 넘어가 blob 을 못 읽고 스트림이 어긋난다
        assertProtocolError(rawFrame("""{"t":"data","blob":"열개"}"""))
    }

    @Test
    fun `헤더가 객체가 아니면 거부한다`() {
        assertProtocolError(rawFrame("[1,2,3]"))
    }

    @Test
    fun `헤더가 JSON 이 아니면 거부한다`() {
        assertProtocolError(rawFrame("헤더가 아니다"))
    }

    @Test
    fun `헤더가 UTF-8 이 아니면 거부한다`() {
        // String(bytes, UTF_8) 은 U+FFFD 로 바꿔치우고 넘어간다. 파이썬은 연결을
        // 끊으므로 여기서도 끊어야 한다
        val broken = """{"t":"x","n":"@"}""".toByteArray(Charsets.UTF_8)
        broken[broken.size - 3] = 0xFF.toByte()
        assertProtocolError(lengthPrefix(broken.size) + broken)
    }

    // --- 끊긴 연결 ---

    @Test
    fun `아무것도 없으면 종료로 본다`() {
        assertPeerClosed(ByteArray(0))
    }

    @Test
    fun `길이만 오고 끊기면 종료로 본다`() {
        assertPeerClosed(lengthPrefix(50))
    }

    @Test
    fun `blob 이 모자란 채로 끊기면 종료로 본다`() {
        val full = Frame.encode(JSONObject().put("t", "data"), ByteArray(1000))
        assertPeerClosed(full.copyOf(full.size - 1))
    }

    // --- 도구 ---

    private fun assertProtocolError(bytes: ByteArray) {
        try {
            FrameReader(ByteArrayInputStream(bytes)).read()
            fail("거부해야 하는 프레임을 받아들였다")
        } catch (_: ProtocolError) {
        }
    }

    private fun assertPeerClosed(bytes: ByteArray) {
        try {
            FrameReader(ByteArrayInputStream(bytes)).read()
            fail("종료를 알려야 한다")
        } catch (_: PeerClosed) {
        }
    }

    private fun lengthPrefix(size: Int) = byteArrayOf(
        (size ushr 24).toByte(),
        (size ushr 16).toByte(),
        (size ushr 8).toByte(),
        size.toByte(),
    )

    /** Frame.encode 를 거치지 않고 헤더 문자열을 그대로 실은 프레임. */
    private fun rawFrame(headerText: String): ByteArray {
        val raw = headerText.toByteArray(Charsets.UTF_8)
        return lengthPrefix(raw.size) + raw
    }

    private fun sha256(bytes: ByteArray) =
        MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

    private fun resource(name: String) =
        checkNotNull(javaClass.classLoader!!.getResourceAsStream("golden/$name")) {
            "golden/$name 이 없습니다. tools/gen-golden.py 를 실행하세요"
        }

    private fun goldenBytes() = resource("frames.bin").use { it.readBytes() }

    private fun goldenManifest() =
        JSONArray(resource("frames.json").use { it.readBytes().toString(Charsets.UTF_8) })

    /**
     * Kotlin 이 만든 프레임을 내보낸다. `tests/test_kotlin_frames.py` 가 파이썬
     * FrameReader 로 읽어 반대 방향을 검증한다.
     *
     * 저장소에 넣지 않는다. 넣어두면 Kotlin 을 고친 뒤 다시 뽑는 것을 잊었을 때
     * 낡은 파일을 검사하며 통과해 버린다.
     */
    private fun emitForPython(bytes: ByteArray, count: Int) {
        val out = File(System.getProperty("user.dir"), "build/golden-out/kotlin-frames.bin")
        out.parentFile?.mkdirs()
        out.writeBytes(bytes)
        println("Kotlin 이 만든 프레임 $count 개 -> ${out.absolutePath} (${bytes.size} 바이트)")
    }

    // --- JSON 깊은 비교 ---

    private fun assertSameJson(path: String, expected: Any?, actual: Any?) {
        when (expected) {
            is JSONObject -> {
                assertTrue("$path: 객체가 아닙니다 ($actual)", actual is JSONObject)
                actual as JSONObject
                assertEquals("$path: 키 집합", keysOf(expected), keysOf(actual))
                for (key in expected.keys()) {
                    assertSameJson("$path.$key", expected.get(key), actual.get(key))
                }
            }
            is JSONArray -> {
                assertTrue("$path: 배열이 아닙니다 ($actual)", actual is JSONArray)
                actual as JSONArray
                assertEquals("$path: 길이", expected.length(), actual.length())
                for (i in 0 until expected.length()) {
                    assertSameJson("$path[$i]", expected.get(i), actual.get(i))
                }
            }
            // 정수를 Int 로 읽을지 Long 으로 읽을지는 파서 사정이다. 값으로 비교한다
            is Number -> {
                assertTrue("$path: 숫자가 아닙니다 ($actual)", actual is Number)
                assertEquals("$path", expected.toDouble(), (actual as Number).toDouble(), 0.0)
            }
            else -> assertEquals(path, expected, actual)
        }
    }

    private fun keysOf(obj: JSONObject) = obj.keys().asSequence().toSortedSet()
}
