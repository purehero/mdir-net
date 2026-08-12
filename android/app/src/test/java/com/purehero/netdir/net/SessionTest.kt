// 대칭 세션 — 진짜 TCP 소켓 쌍에 Session 둘을 물려 돌린다
//
// 제일 중요한 것은 `동시에 양방향으로 전송한다` 다. 양쪽이 각자 1번부터 id 를
// 매기므로, `q` 규칙과 우편함 분리가 없으면 여기서 응답이 섞인다.

package com.purehero.netdir.net

import java.io.File
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.security.MessageDigest
import java.util.concurrent.CopyOnWriteArrayList
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.cancel
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import com.purehero.netdir.store.LocalFileStore
import com.purehero.netdir.store.RemotePath
import com.purehero.netdir.store.ShareList
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class SessionTest {

    @get:Rule
    val temp = TemporaryFolder()

    private lateinit var scope: CoroutineScope
    private lateinit var dirA: File
    private lateinit var dirB: File
    private lateinit var a: Session
    private lateinit var b: Session

    private val eventsA = CopyOnWriteArrayList<SessionEvent>()
    private val eventsB = CopyOnWriteArrayList<SessionEvent>()

    @Before
    fun setUp() {
        scope = CoroutineScope(Dispatchers.IO + SupervisorJob())

        dirA = temp.newFolder("폰")
        dirB = temp.newFolder("맥")

        val (clientSocket, serverSocket) = socketPair()
        a = Session(clientSocket, "맥", "127.0.0.1", store(dirA, "내 폰"), scope) { eventsA.add(it) }
        b = Session(serverSocket, "폰", "127.0.0.1", store(dirB, "맥 문서"), scope) { eventsB.add(it) }
        a.start()
        b.start()
    }

    @After
    fun tearDown() {
        runCatching { a.close() }
        runCatching { b.close() }
        scope.cancel()
    }

    // --- 평범한 요청 ---

    @Test
    fun `home 은 늘 가상 루트다`() = runBlocking {
        // 상대에게 내 실제 경로를 알려주지 않는다
        assertEquals(RemotePath.ROOT, within { a.remoteHome() })
    }

    @Test
    fun `가상 루트를 목록으로 받는다`() = runBlocking {
        val (path, entries) = within { a.remoteList(".") }

        assertEquals(".", path)
        assertEquals(listOf("맥 문서"), entries.map { it.name })
        assertTrue(entries[0].isDir)
    }

    @Test
    fun `공유 폴더 안을 목록으로 받는다`() = runBlocking {
        File(dirB, "메모.txt").writeText("안녕")
        File(dirB, "하위").mkdir()

        val (path, entries) = within { a.remoteList("맥 문서") }

        assertEquals("맥 문서", path)
        assertEquals(listOf("하위", "메모.txt"), entries.map { it.name })
        assertEquals("안녕".toByteArray().size.toLong(), entries.last().size)
    }

    @Test
    fun `목록의 경로는 정규화되어 돌아온다`() = runBlocking {
        // 상대가 그대로 화면에 쓰고 다음 요청에 다시 보내기 때문이다
        val (path, _) = within { a.remoteList("맥 문서/하위/..") }
        assertEquals("맥 문서", path)
    }

    @Test
    fun `stat 을 받는다`() = runBlocking {
        File(dirB, "파일.bin").writeBytes(ByteArray(1234))

        val exists = within { a.remoteStat("맥 문서/파일.bin") }
        assertTrue(exists.exists)
        assertFalse(exists.isDir)
        assertEquals(1234L, exists.size)

        val missing = within { a.remoteStat("맥 문서/없다.bin") }
        assertFalse(missing.exists)
    }

    @Test
    fun `재귀 목록에 빈 폴더도 들어온다`() = runBlocking {
        File(dirB, "안").mkdirs()
        File(dirB, "안/파일.txt").writeText("x")
        File(dirB, "빈 폴더").mkdir()

        val tree = within { a.remoteTree("맥 문서") }

        assertEquals(listOf("안/파일.txt"), tree.files.map { it.path })
        assertEquals(listOf("빈 폴더", "안"), tree.dirs)
    }

    @Test
    fun `뷰어용으로 앞부분만 받는다`() = runBlocking {
        File(dirB, "큰것.bin").writeBytes(ByteArray(5000) { it.toByte() })

        val head = within { a.remoteHead("맥 문서/큰것.bin", 100) }

        assertEquals(5000L, head.size)
        assertEquals(100, head.bytes.size)
    }

    /** 음수를 그대로 넘기면 파이썬에서 `read(-1)` 이 되어 파일을 통째로 읽는다. */
    @Test
    fun `head 의 한도를 서버가 붙잡는다`() = runBlocking {
        File(dirB, "큰것.bin").writeBytes(ByteArray(5000))

        assertEquals(0, within { a.remoteHead("맥 문서/큰것.bin", -1) }.bytes.size)
        assertEquals(
            "상한을 넘겨도 잘려야 한다",
            0,
            within { a.remoteHead("맥 문서/큰것.bin", Int.MAX_VALUE) }.bytes.size - 5000,
        )
    }

    // --- 바꾸는 요청 ---

    @Test
    fun `원격에 폴더를 만든다`() = runBlocking {
        within { a.remoteMkdir("맥 문서/새 폴더") }
        assertTrue(File(dirB, "새 폴더").isDirectory)
        assertTrue(eventsB.any { it is SessionEvent.Changed })
    }

    @Test
    fun `원격 항목을 지운다`() = runBlocking {
        File(dirB, "버릴것").mkdir()
        File(dirB, "버릴것/안에.txt").writeText("x")

        within { a.remoteDelete("맥 문서/버릴것") }
        assertFalse(File(dirB, "버릴것").exists())
    }

    @Test
    fun `원격 이름을 바꾼다`() = runBlocking {
        File(dirB, "전.txt").writeText("내용")

        within { a.remoteRename("맥 문서/전.txt", "후.txt") }
        assertFalse(File(dirB, "전.txt").exists())
        assertEquals("내용", File(dirB, "후.txt").readText())
    }

    // --- 전송 ---

    @Test
    fun `상대 파일을 받아온다`() = runBlocking {
        val payload = filler(700_000, "pull")
        File(dirB, "큰 파일.bin").writeBytes(payload)

        var progress = 0L
        val got = within(30_000) {
            a.pullFile("맥 문서/큰 파일.bin", "내 폰/받은 것.bin") { progress += it }
        }

        assertEquals(payload.size.toLong(), got)
        assertEquals(payload.size.toLong(), progress)
        assertEquals(sha(payload), sha(File(dirA, "받은 것.bin").readBytes()))
        assertFalse(".part 가 남았다", File(dirA, "받은 것.bin.part").exists())
    }

    @Test
    fun `내 파일을 밀어넣는다`() = runBlocking {
        val payload = filler(700_000, "push")
        File(dirA, "보낼 것.bin").writeBytes(payload)

        val sent = within(30_000) { a.pushFile("내 폰/보낼 것.bin", "맥 문서/받은 것.bin") }

        assertEquals(payload.size.toLong(), sent)
        assertEquals(sha(payload), sha(File(dirB, "받은 것.bin").readBytes()))
        assertFalse(File(dirB, "받은 것.bin.part").exists())
        assertTrue(eventsB.any { it is SessionEvent.Received })
    }

    @Test
    fun `밀어넣기는 부모 폴더를 만든다`() = runBlocking {
        File(dirA, "작은것.txt").writeText("내용")

        within { a.pushFile("내 폰/작은것.txt", "맥 문서/새로/생길/곳/파일.txt") }

        assertEquals("내용", File(dirB, "새로/생길/곳/파일.txt").readText())
    }

    /**
     * 여기가 핵심이다.
     *
     * 양쪽이 각자 1번부터 id 를 매기므로 A 의 pull(id=1)과 B 의 pull(id=1)이 겹친다.
     * `q` 규칙과 우편함 분리가 없으면 A 가 받는 데이터에 B 의 요청이 섞여 들어간다.
     */
    @Test
    fun `양쪽이 동시에 주고받아도 섞이지 않는다`() = runBlocking {
        val fromB = filler(800_000, "B가 가진 것")
        val fromA = filler(650_000, "A가 가진 것")
        File(dirB, "B파일.bin").writeBytes(fromB)
        File(dirA, "A파일.bin").writeBytes(fromA)

        within(60_000) {
            listOf(
                async { a.pullFile("맥 문서/B파일.bin", "내 폰/B에서받음.bin") },
                async { b.pullFile("내 폰/A파일.bin", "맥 문서/A에서받음.bin") },
                async { a.pushFile("내 폰/A파일.bin", "맥 문서/A가밀어넣음.bin") },
                async { b.pushFile("맥 문서/B파일.bin", "내 폰/B가밀어넣음.bin") },
            ).awaitAll()
        }

        assertEquals("A 가 받은 것", sha(fromB), sha(File(dirA, "B에서받음.bin").readBytes()))
        assertEquals("B 가 받은 것", sha(fromA), sha(File(dirB, "A에서받음.bin").readBytes()))
        assertEquals("A 가 민 것", sha(fromA), sha(File(dirB, "A가밀어넣음.bin").readBytes()))
        assertEquals("B 가 민 것", sha(fromB), sha(File(dirA, "B가밀어넣음.bin").readBytes()))
    }

    /**
     * 전송이 리더 코루틴을 붙잡으면 안 된다.
     *
     * 요청 처리를 읽기 루프에서 직접 했다면 2MB 를 다 보낼 때까지 다른 요청이
     * 하나도 못 지나가고 타임아웃 난다.
     */
    @Test
    fun `전송 중에도 다른 요청이 지나간다`() = runBlocking {
        val payload = filler(2_000_000, "동시")
        File(dirB, "큰것.bin").writeBytes(payload)
        File(dirB, "메모.txt").writeText("x")

        val listings = within(60_000) {
            val transfer = async { a.pullFile("맥 문서/큰것.bin", "내 폰/받음.bin") }
            val queries = (1..5).map { async { a.remoteList("맥 문서") } }

            val results = queries.awaitAll()
            assertEquals(payload.size.toLong(), transfer.await())
            results
        }

        for (result in listings) {
            assertTrue("전송 중 목록이 비었다", result.second.any { it.name == "메모.txt" })
        }
        assertEquals(sha(payload), sha(File(dirA, "받음.bin").readBytes()))
    }

    /**
     * 상대가 `push.ok` 를 기다리지 않고 곧바로 데이터를 보내도 받아내야 한다.
     *
     * 이것이 "우편함 등록을 읽기 루프에서 먼저 한다" 는 규칙(PROTOCOL.md 5.1)이
     * 실제로 필요한 경우다. 등록을 처리 코루틴에 맡기면, 뒤따라 도착한 data 프레임이
     * 갈 곳이 없어 조용히 버려지고 전송이 30초 뒤 타임아웃으로 끝난다.
     *
     * 우리 구현끼리는 `push.ok` 를 기다리므로 이 경로가 안 드러난다. 규격을 느슨하게
     * 지키는 다른 구현이 붙었을 때를 대비한다.
     */
    @Test
    fun `push_ok 를 기다리지 않는 상대도 받아낸다`() = runBlocking {
        val payload = filler(600_000, "성급한 상대")
        val dir = temp.newFolder("성급")
        val (rawSocket, sessionSocket) = socketPair()

        val session = Session(sessionSocket, "성급이", "127.0.0.1", store(dir, "받는곳"), scope) {}
        session.start()

        try {
            val out = rawSocket.getOutputStream()
            val reader = com.purehero.netdir.protocol.FrameReader(rawSocket.getInputStream())

            // push.begin 을 보내고 **응답을 기다리지 않고** 곧바로 데이터를 밀어넣는다
            com.purehero.netdir.protocol.Frame.send(
                out,
                org.json.JSONObject().put("t", "push.begin").put("id", 1).put("q", 1)
                    .put("path", "받는곳/성급하게.bin").put("size", payload.size),
            )
            var at = 0
            while (at < payload.size) {
                val take = minOf(64 * 1024, payload.size - at)
                com.purehero.netdir.protocol.Frame.send(
                    out,
                    org.json.JSONObject().put("t", "data").put("id", 1).put("q", 1),
                    payload.copyOfRange(at, at + take),
                )
                at += take
            }
            com.purehero.netdir.protocol.Frame.send(
                out,
                org.json.JSONObject().put("t", "data.end").put("id", 1).put("q", 1),
            )

            rawSocket.soTimeout = 15_000
            var done = false
            while (!done) {
                val header = reader.read().header
                when (header.optString("t")) {
                    "push.done" -> done = true
                    "err" -> fail("오류로 끝났다: ${header.optString("msg")}")
                }
            }

            assertEquals(sha(payload), sha(File(dir, "성급하게.bin").readBytes()))
        } finally {
            runCatching { session.close() }
            runCatching { rawSocket.close() }
        }
    }

    // --- 오류 ---

    @Test
    fun `거부된 경로는 오류로 돌아온다`() = runBlocking {
        val message = deniedMessage { a.remoteList("없는공유") }
        assertTrue("이유가 담겨야 한다: $message", message.contains("공유 목록에 없습니다"))
    }

    /** 답을 안 보내면 상대는 30초 동안 얼어붙는다. 어떤 예외든 회신해야 한다. */
    @Test
    fun `어떤 실패든 답이 온다`() = runBlocking {
        val cases = listOf<suspend () -> Unit>(
            { a.remoteList("맥 문서/없는폴더") },
            { a.remoteHead("맥 문서", 10) },
            { a.remoteDelete("맥 문서") },
            { a.remoteDelete(".") },
            { a.remoteRename("맥 문서", "새이름") },
            { a.remoteMkdir(".") },
            { a.remoteTree(".") },
            { a.pushFile("내 폰/없는파일.bin", "맥 문서/x.bin") },
            { a.pullFile("맥 문서/없는파일.bin", "내 폰/x.bin") },
        )

        for ((index, case) in cases.withIndex()) {
            // 각각이 REQUEST_TIMEOUT 을 기다리지 않고 즉시 실패해야 한다
            val elapsed = System.nanoTime()
            deniedMessage(index) { case() }
            val tookMs = (System.nanoTime() - elapsed) / 1_000_000
            assertTrue("[$index] 답이 없어 타임아웃까지 기다렸다 (${tookMs}ms)", tookMs < 5_000)
        }
    }

    @Test
    fun `오류가 나도 세션은 살아있다`() = runBlocking {
        deniedMessage { a.remoteList("없는공유") }

        // 이어서 멀쩡한 요청이 되어야 한다
        assertEquals(RemotePath.ROOT, within { a.remoteHome() })
        assertFalse(a.isClosed)
    }

    @Test
    fun `밀어넣다 거부되면 반쪽짜리를 남기지 않는다`() = runBlocking {
        File(dirA, "보낼것.bin").writeBytes(ByteArray(1000))

        deniedMessage { a.pushFile("내 폰/보낼것.bin", "맥 문서/../밖으로.bin") }

        assertEquals(
            "공유 폴더에 무언가 생겼다",
            emptyList<String>(),
            dirB.list()!!.toList(),
        )
    }

    // --- 끊기 ---

    @Test
    fun `닫으면 기다리던 요청이 깨어난다`() = runBlocking {
        within(10_000) {
            val pending = async {
                try {
                    a.remoteList("맥 문서")
                    "성공"
                } catch (exc: SessionError) {
                    "끊김"
                }
            }
            a.close("테스트")
            pending.await()
        }
        assertTrue(a.isClosed)
        assertTrue(eventsA.any { it is SessionEvent.Disconnected })
    }

    @Test
    fun `닫힌 세션에 요청하면 곧바로 실패한다`() = runBlocking {
        a.close("테스트")
        val message = deniedMessage { a.remoteHome() }
        assertTrue(message, message.contains("연결이 끊어졌습니다"))
    }

    @Test
    fun `한쪽이 끊으면 다른 쪽도 안다`() = runBlocking {
        a.close("테스트")
        withTimeout(5_000) {
            while (!b.isClosed) kotlinx.coroutines.delay(20)
        }
        assertTrue(eventsB.any { it is SessionEvent.Disconnected })
    }

    // --- 도구 ---

    private fun store(dir: File, shareName: String) =
        LocalFileStore(ShareList().apply { add(dir.absolutePath, shareName) })

    private fun socketPair(): Pair<Socket, Socket> {
        val listener = ServerSocket()
        listener.reuseAddress = true
        listener.bind(InetSocketAddress("127.0.0.1", 0), 1)

        listener.use {
            val client = Socket()
            client.connect(InetSocketAddress("127.0.0.1", listener.localPort), 2_000)
            return client to listener.accept()
        }
    }

    private suspend fun <T> within(timeoutMs: Long = 10_000, body: suspend () -> T): T =
        withTimeout(timeoutMs) { body() }

    /** 실패해야 하는 호출. 오류 메시지를 돌려준다. */
    private suspend fun deniedMessage(index: Int = 0, body: suspend () -> Unit): String {
        try {
            withTimeout(20_000) { body() }
        } catch (exc: SessionError) {
            return exc.message ?: ""
        } catch (exc: com.purehero.netdir.store.PathDenied) {
            // 내 쪽 저장소가 먼저 거부한 경우 (예: 없는 파일을 밀어넣기)
            return exc.message ?: ""
        }
        fail("[$index] 실패해야 하는데 성공했다")
        return ""
    }

    private fun filler(size: Int, seed: String): ByteArray {
        val out = ByteArray(size)
        var block = MessageDigest.getInstance("SHA-256").digest(seed.toByteArray())
        var at = 0
        while (at < size) {
            val take = minOf(block.size, size - at)
            block.copyInto(out, at, 0, take)
            at += take
            block = MessageDigest.getInstance("SHA-256").digest(block)
        }
        return out
    }

    private fun sha(bytes: ByteArray) =
        MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
}
