// 세션의 저장소 둘이 실제로 갈라져 있는지 — 상대가 보는 곳과 내가 받아 놓는 곳
//
// 폰에서 `내 폰` 뿌리를 작업 폴더로 바꾸면서 이 둘이 달라졌다. 상대는 공유 목록만
// 봐야 하고(보안 경계), 내가 받아오는 파일은 작업 폴더 어디에나 놓을 수 있어야 한다.
//
// 이 파일이 없으면 조용히 되돌아간다. 실제로 한 번 그랬다 — `Transfers` 에 넘긴
// 저장소는 tree/mkdir 에만 쓰이고 파일 읽기·쓰기는 `Session` 이 자기 것으로 했다.
// 상대 요청 경로만 검증하면 안 잡히고, 폰 두 대를 붙여봐야 드러났다.

package com.purehero.netdir.net

import com.purehero.netdir.store.LocalFileStore
import com.purehero.netdir.store.ShareList
import java.io.File
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class LocalStoreSplitTest {

    @get:Rule
    val temp = TemporaryFolder()

    private lateinit var scope: CoroutineScope

    /** 내가 상대에게 열어주는 곳. 이름은 `공유`. */
    private lateinit var shared: File

    /** 내 작업 폴더. 이름은 `작업`. 상대는 이 이름을 몰라야 한다. */
    private lateinit var work: File

    private lateinit var me: Session
    private lateinit var peer: Session

    @Before
    fun setUp() {
        scope = CoroutineScope(Dispatchers.IO + SupervisorJob())

        shared = temp.newFolder("공유")
        work = temp.newFolder("작업")
        File(temp.newFolder("상대"), "보낼것.txt")

        val peerDir = temp.newFolder("상대것")
        File(peerDir, "받아올것.txt").writeText("상대가 가진 내용")

        val (mine, theirs) = socketPair()
        me = Session(
            socket = mine,
            peerName = "상대",
            peerHost = "127.0.0.1",
            store = store(shared, "공유"),
            scope = scope,
            localStore = store(work, "작업"),
            onEvent = {},
        )
        peer = Session(theirs, "나", "127.0.0.1", store(peerDir, "상대공유"), scope) {}
        me.start()
        peer.start()
    }

    @After
    fun tearDown() {
        runCatching { me.close() }
        runCatching { peer.close() }
        scope.cancel()
    }

    @Test
    fun `상대는 공유 목록만 본다 — 작업 폴더는 보이지 않는다`() = runBlocking {
        val (_, entries) = within { peer.remoteList(".") }

        assertEquals(listOf("공유"), entries.map { it.name })
    }

    @Test
    fun `상대는 작업 폴더 이름으로 들어올 수 없다`() = runBlocking {
        try {
            within { peer.remoteList("작업") }
            fail("작업 폴더가 상대에게 열렸다")
        } catch (exc: Exception) {
            assertTrue(
                "예상 밖 메시지: ${exc.message}",
                exc.message.orEmpty().contains("공유 목록에 없습니다"),
            )
        }
    }

    @Test
    fun `내가 받아오는 파일은 작업 폴더에 놓인다`() = runBlocking {
        // 여기가 회귀 지점이다. 받는 자리는 공유 목록이 아니라 작업 폴더로 해석돼야 한다
        val moved = within { me.pullFile("상대공유/받아올것.txt", "작업/받은것.txt") }

        val landed = File(work, "받은것.txt")
        assertTrue("작업 폴더에 안 놓였다", landed.isFile)
        assertEquals("상대가 가진 내용", landed.readText())
        assertEquals(landed.length(), moved)

        // 공유 폴더에는 아무것도 생기지 않는다
        assertEquals(emptyList<String>(), shared.list()!!.toList())
    }

    @Test
    fun `내가 보내는 파일도 작업 폴더에서 읽는다`() = runBlocking {
        File(work, "보낼것.txt").writeText("내 작업 폴더의 내용")

        within { me.pushFile("작업/보낼것.txt", "상대공유/받은것.txt") }

        val landed = File(temp.root, "상대것/받은것.txt")
        assertTrue("상대에게 안 갔다", landed.isFile)
        assertEquals("내 작업 폴더의 내용", landed.readText())
    }

    @Test
    fun `나누지 않으면 둘 다 같은 곳이다`() = runBlocking {
        // 데스크톱과 기존 테스트는 localStore 를 주지 않는다. 그때는 예전대로 돈다
        val (mine, theirs) = socketPair()
        val one = temp.newFolder("하나")
        val single = Session(mine, "상대", "127.0.0.1", store(one, "하나"), scope) {}
        val other = Session(theirs, "나", "127.0.0.1", store(temp.newFolder("둘"), "둘"), scope) {}
        single.start()
        other.start()

        try {
            File(one, "가진것.txt").writeText("내용")
            within { single.pushFile("하나/가진것.txt", "둘/받은것.txt") }
            assertTrue(File(temp.root, "둘/받은것.txt").isFile)
        } finally {
            runCatching { single.close() }
            runCatching { other.close() }
        }
    }

    private suspend fun <T> within(body: suspend () -> T): T = withTimeout(10_000) { body() }

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
}
