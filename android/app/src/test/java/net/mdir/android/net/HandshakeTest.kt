// 핸드셰이크 — 승인/거부/만료/버전 불일치, 그리고 포트 잡기 (PROTOCOL.md 4절, 8절)

package net.mdir.android.net

import java.io.File
import java.net.InetSocketAddress
import java.net.Socket
import java.util.concurrent.CopyOnWriteArrayList
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import net.mdir.android.protocol.Config
import net.mdir.android.protocol.Frame
import net.mdir.android.protocol.FrameReader
import net.mdir.android.store.FileStore
import net.mdir.android.store.LocalFileStore
import net.mdir.android.store.RemotePath
import net.mdir.android.store.ShareList
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class HandshakeTest {

    @get:Rule
    val temp = TemporaryFolder()

    private lateinit var scope: CoroutineScope
    private lateinit var serverDir: File
    private lateinit var clientDir: File
    private lateinit var server: Server

    private val events = CopyOnWriteArrayList<ServerEvent>()

    @Before
    fun setUp() {
        scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
        serverDir = temp.newFolder("맥")
        clientDir = temp.newFolder("폰")
        File(serverDir, "맥에있는것.txt").writeText("안녕")
    }

    @After
    fun tearDown() {
        runCatching { server.stop() }
        scope.cancel()
    }

    /** 승인/거부를 자동으로 눌러주는 서버를 세운다. null 이면 아무도 안 누른다. */
    private fun startServer(answer: Boolean?, approvalTimeoutMs: Long = 500): Int {
        server = Server(
            myName = { "맥" },
            store = { store(serverDir, "맥 문서") },
            scope = scope,
            basePort = 0, // 커널이 비어있는 포트를 준다
            approvalTimeoutMs = approvalTimeoutMs,
            onEvent = { event ->
                events.add(event)
                if (event is ServerEvent.Approval && answer != null) event.request.decide(answer)
            },
        )
        val port = server.bind()
        server.start()
        return port
    }

    private fun store(dir: File, name: String): FileStore =
        LocalFileStore(ShareList().apply { add(dir.absolutePath, name) })

    private fun peer(port: Int) = Peer("서버", "맥", "127.0.0.1", port, "darwin", 0L)

    // --- 승인 ---

    @Test
    fun `승인하면 양쪽이 서로에게 요청할 수 있다`() = runBlocking {
        val port = startServer(answer = true)

        val session = withTimeout(10_000) {
            connect(peer(port), "내 폰", store(clientDir, "내 폰"), scope)
        }

        try {
            // 요청한 쪽 -> 받은 쪽
            assertEquals(RemotePath.ROOT, withTimeout(5_000) { session.remoteHome() })
            val (_, entries) = withTimeout(5_000) { session.remoteList("맥 문서") }
            assertEquals(listOf("맥에있는것.txt"), entries.map { it.name })

            // 응답의 name 을 상대 이름으로 쓴다
            assertEquals("맥", session.peerName)

            // 받은 쪽 -> 요청한 쪽 (대칭이다)
            val accepted = events.filterIsInstance<ServerEvent.Connected>().single().session
            val (_, mine) = withTimeout(5_000) { accepted.remoteList(".") }
            assertEquals(listOf("내 폰"), mine.map { it.name })
            assertEquals("내 폰", accepted.peerName)
        } finally {
            session.close()
        }
    }

    @Test
    fun `승인 요청에 상대 정보가 담긴다`() = runBlocking {
        val port = startServer(answer = true)
        val session = withTimeout(10_000) {
            connect(peer(port), "내 폰", store(clientDir, "내 폰"), scope)
        }
        session.close()

        val approval = events.filterIsInstance<ServerEvent.Approval>().single().request
        assertEquals("내 폰", approval.name)
        assertEquals("127.0.0.1", approval.host)
        assertEquals("android", approval.platform)
    }

    // --- 거부 ---

    @Test
    fun `거부하면 연결되지 않는다`() = runBlocking {
        val port = startServer(answer = false)

        try {
            withTimeout(10_000) { connect(peer(port), "내 폰", store(clientDir, "내 폰"), scope) }
            fail("거부됐는데 연결됐다")
        } catch (_: Rejected) {
        }

        assertTrue(events.none { it is ServerEvent.Connected })
    }

    /**
     * 아무도 안 누르면 거부로 처리하되 **화면에도 만료를 알려야 한다.**
     *
     * 알리지 않으면 사용자가 늦게 수락을 눌렀을 때 창만 닫히고 아무 일도 일어나지
     * 않아 승인됐다고 믿게 된다.
     */
    @Test
    fun `아무도 안 누르면 만료되고 화면에 알린다`() = runBlocking {
        val port = startServer(answer = null, approvalTimeoutMs = 300)

        try {
            withTimeout(10_000) { connect(peer(port), "내 폰", store(clientDir, "내 폰"), scope) }
            fail("만료됐는데 연결됐다")
        } catch (_: Rejected) {
        }

        assertEquals(1, events.filterIsInstance<ServerEvent.ApprovalExpired>().size)
        assertTrue(events.none { it is ServerEvent.Connected })
    }

    @Test
    fun `만료된 뒤에 눌러도 연결되지 않는다`() = runBlocking {
        val port = startServer(answer = null, approvalTimeoutMs = 200)

        val attempt = runCatching {
            withTimeout(10_000) { connect(peer(port), "내 폰", store(clientDir, "내 폰"), scope) }
        }
        assertTrue(attempt.isFailure)

        // 늦게 누른다
        events.filterIsInstance<ServerEvent.Approval>().single().request.decide(true)
        delay(300)

        assertTrue("늦게 눌렀는데 세션이 생겼다", events.none { it is ServerEvent.Connected })
    }

    // --- 규격을 어긴 상대 ---

    @Test
    fun `버전이 다르면 이유를 알려주고 끊는다`() = runBlocking {
        val port = startServer(answer = true)

        val header = rawHello(port, JSONObject().put("t", "hello").put("v", 2).put("name", "미래"))

        assertEquals("hello.no", header.optString("t"))
        assertTrue("이유가 없다", header.optString("msg").contains("버전"))
        // 승인 창을 띄우지 않는다. 버전이 안 맞으면 물어볼 것도 없다
        assertTrue(events.none { it is ServerEvent.Approval })
    }

    @Test
    fun `hello 가 아니면 끊는다`() = runBlocking {
        val port = startServer(answer = true)

        val header = rawHello(port, JSONObject().put("t", "list").put("v", 1).put("id", 1))

        assertEquals("hello.no", header.optString("t"))
        assertTrue(events.none { it is ServerEvent.Approval })
    }

    // --- 포트 (PROTOCOL.md 8절) ---

    @Test
    fun `쓰는 중이면 다음 포트를 잡는다`() = runBlocking {
        val first = startServer(answer = true)

        val second = Server(
            myName = { "맥2" },
            store = { store(serverDir, "맥 문서") },
            scope = scope,
            basePort = first,
            onEvent = {},
        )
        try {
            val port = second.bind()
            assertEquals("한 칸 올려 잡아야 한다", first + 1, port)
            assertEquals("실제로 잡은 포트를 알려야 한다", port, second.port)
        } finally {
            second.stop()
        }
    }

    // --- 도구 ---

    /** 세션을 만들지 않고 hello 만 손으로 보내 응답 헤더를 본다. */
    private fun rawHello(port: Int, hello: JSONObject): JSONObject {
        Socket().use { socket ->
            socket.connect(InetSocketAddress("127.0.0.1", port), 5_000)
            socket.soTimeout = 5_000
            Frame.send(socket.getOutputStream(), hello)
            return FrameReader(socket.getInputStream()).read().header
        }
    }
}
