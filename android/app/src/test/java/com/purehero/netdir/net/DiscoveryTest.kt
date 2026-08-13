// 탐색 — 파이썬 알림을 읽는지, 규격 위반을 버리는지, 실제 UDP 로 오가는지

package com.purehero.netdir.net

import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import com.purehero.netdir.protocol.Config
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class DiscoveryTest {

    private val loopback: InetAddress = InetAddress.getByName("127.0.0.1")

    // --- 알림 해석 ---

    /**
     * 파이썬이 실제로 내보내는 바이트를 읽어낸다.
     *
     * 탐색 데이터그램은 기본 `json.dumps` 라 한글이 `\uXXXX` 로 escape 되고
     * 구분자에 공백이 들어간다. 프레임 헤더와 규칙이 다른 곳이라 따로 고정한다.
     */
    @Test
    fun `파이썬이 만든 알림을 그대로 읽는다`() {
        val raw = golden("announce.bin")
        val expected = JSONObject(String(golden("announce.json"), Charsets.UTF_8))

        // 프레임 규칙만 보고 만들면 놓치는 지점이다. 골든이 실제로 escape 형태인지 확인
        assertTrue(
            "골든이 escape 형태가 아니다. tools/gen-golden.py 를 다시 실행하라",
            String(raw, Charsets.UTF_8).contains("\\uac70"),
        )

        val peer = Announce.parse(raw, raw.size, "192.168.0.7", 1000L)

        assertNotNull("파이썬 알림을 못 읽었다", peer)
        assertEquals(expected.getString("id"), peer!!.nodeId)
        assertEquals(expected.getString("name"), peer.name)
        assertEquals(expected.getInt("port"), peer.port)
        assertEquals(expected.getString("platform"), peer.platform)
        // 주소는 페이로드가 아니라 출발지 IP 에서 온다
        assertEquals("192.168.0.7", peer.host)
    }

    @Test
    fun `내가 만든 알림을 스스로 다시 읽는다`() {
        val raw = Announce.encode("aabbccdd00112233", "내 폰", 9605, "android")
        val peer = Announce.parse(raw, raw.size, "10.0.0.5", 1L)!!

        assertEquals("aabbccdd00112233", peer.nodeId)
        assertEquals("내 폰", peer.name)
        assertEquals(9605, peer.port)
        assertEquals("android", peer.platform)

        // 파이썬 Discovery 가 이걸 받아들이는지는 tests/test_kotlin_interop.py 가 본다
        emitForPython(raw)
    }

    /**
     * Kotlin 이 만든 알림을 내보낸다. 저장소에 넣지 않는다 — 넣어두면 Kotlin 을
     * 고친 뒤 다시 뽑는 것을 잊었을 때 낡은 파일을 검사하며 통과해 버린다.
     */
    private fun emitForPython(payload: ByteArray) {
        val out = java.io.File(
            System.getProperty("user.dir"),
            "build/golden-out/kotlin-announce.bin",
        )
        out.parentFile?.mkdirs()
        out.writeBytes(payload)
    }

    @Test
    fun `규격에 맞지 않는 알림은 버린다`() {
        val bad = mapOf(
            // 상수에서 파생시킨다. 숫자로 박아두면 버전을 올릴 때 "다른 버전" 이
            // 어느새 "맞는 버전" 이 되어 테스트가 조용히 무의미해진다
            "버전이 다르다" to """{"v":${Config.PROTOCOL_VERSION + 1},"id":"aa","port":9600}""",
            "버전이 문자열이다" to """{"v":"${Config.PROTOCOL_VERSION}","id":"aa","port":9600}""",
            "버전이 없다" to """{"id":"aa","port":9600}""",
            "id 가 없다" to """{"v":${Config.PROTOCOL_VERSION},"port":9600}""",
            "id 가 문자열이 아니다" to """{"v":${Config.PROTOCOL_VERSION},"id":7,"port":9600}""",
            "포트가 없다" to """{"v":${Config.PROTOCOL_VERSION},"id":"aa"}""",
            "포트가 문자열이다" to """{"v":${Config.PROTOCOL_VERSION},"id":"aa","port":"9600"}""",
            "JSON 이 아니다" to "그냥 글자",
            "객체가 아니다" to """[1,2,3]""",
            "비어있다" to "",
        )

        for ((why, text) in bad) {
            val raw = text.toByteArray(Charsets.UTF_8)
            assertNull(why, Announce.parse(raw, raw.size, "10.0.0.5", 1L))
        }
    }

    @Test
    fun `이름이 비면 주소로 대신한다`() {
        val raw = """{"v":${Config.PROTOCOL_VERSION},"id":"aa","port":9600,"name":""}""".toByteArray(Charsets.UTF_8)
        assertEquals("10.0.0.5", Announce.parse(raw, raw.size, "10.0.0.5", 1L)!!.name)
    }

    /**
     * 본문 전체가 객체 하나여야 한다.
     *
     * `JSONObject(text)` 는 첫 객체만 읽고 뒤를 조용히 버린다. 파이썬 `json.loads` 는
     * "Extra data" 로 거부하므로 그냥 두면 동작이 갈린다. 실제로 이 테스트가 그
     * 차이를 잡아냈다.
     */
    @Test
    fun `뒤에 남은 것이 있으면 버린다`() {
        val real = Announce.encode("aabb", "폰", 9600, "android")

        // 버퍼 재사용으로 지난 패킷의 꼬리가 남은 모양
        val buffer = ByteArray(4096) { 0x7A }
        real.copyInto(buffer)

        assertNotNull("length 만큼만 읽으면 정상이다", Announce.parse(buffer, real.size, "10.0.0.5", 1L))
        assertNull("뒤에 쓰레기가 붙었다", Announce.parse(buffer, buffer.size, "10.0.0.5", 1L))

        val twice = """{"v":${Config.PROTOCOL_VERSION},"id":"aa","port":9600} {"v":${Config.PROTOCOL_VERSION},"id":"bb","port":9601}"""
            .toByteArray(Charsets.UTF_8)
        assertNull("객체가 둘이다", Announce.parse(twice, twice.size, "10.0.0.5", 1L))
    }

    @Test
    fun `뒤쪽 공백은 허용한다`() {
        // 파이썬 json.loads 도 뒤쪽 공백은 받아준다. 거기까지 막으면 지나치다
        val padded = ("""{"v":${Config.PROTOCOL_VERSION},"id":"aa","port":9600}""" + "  \n").toByteArray(Charsets.UTF_8)
        assertNotNull(Announce.parse(padded, padded.size, "10.0.0.5", 1L))
    }

    // --- 피어 표 ---

    @Test
    fun `처음 본 피어만 변화로 친다`() {
        val table = PeerTable()
        val peer = peer("aa", "폰", 1000L)

        assertTrue("처음 봤다", table.observe(peer))
        assertFalse("시각만 바뀐 것은 알릴 일이 아니다", table.observe(peer.copy(lastSeenMs = 2000L)))
        assertTrue("이름이 바뀌었다", table.observe(peer.copy(name = "새 이름", lastSeenMs = 3000L)))
        assertTrue("포트가 바뀌었다", table.observe(peer.copy(name = "새 이름", port = 9601, lastSeenMs = 4000L)))
    }

    @Test
    fun `TTL 이 지나면 지운다`() {
        val table = PeerTable(ttlMs = 6000L)
        table.observe(peer("aa", "폰", 1000L))

        assertFalse("아직 살아있다", table.prune(7000L))
        assertEquals(1, table.snapshot().size)

        assertTrue("지웠어야 한다", table.prune(7001L))
        assertEquals(0, table.snapshot().size)
    }

    @Test
    fun `이름순으로 정렬한다`() {
        val table = PeerTable()
        table.observe(peer("1", "zeta", 0L))
        table.observe(peer("2", "Alpha", 0L))
        table.observe(peer("3", "middle", 0L))

        assertEquals(listOf("Alpha", "middle", "zeta"), table.snapshot().map { it.name })
    }

    // --- 실제 UDP ---

    /** 밖에서 들어온 알림이 목록에 오른다. 주소는 출발지 IP 에서 온다. */
    @Test
    fun `들어온 알림을 실제 소켓으로 받는다`() = runBlocking {
        val port = freePort()
        val discovery = Discovery(
            nodeId = "0000000000000000",
            name = "테스트",
            tcpPort = 9600,
            announcePort = port,
            listenPort = port,
            routes = { listOf(BroadcastRoute(loopback, listOf(loopback))) },
        )

        withScope { scope ->
            discovery.start(scope)
            awaitListening(discovery)

            send(Announce.encode("ffffffffffffffff", "거실 맥북", 9603, "darwin"), port)

            val peers = withTimeout(5_000) { discovery.peers.first { it.isNotEmpty() } }
            assertEquals(1, peers.size)
            assertEquals("거실 맥북", peers[0].name)
            assertEquals(9603, peers[0].port)
            assertEquals("127.0.0.1", peers[0].host)
        }
    }

    /**
     * 내 알림이 나에게 돌아온 것은 무시한다.
     *
     * 루프백으로 알리므로 announcer 가 보낸 것을 listener 가 그대로 되받는다.
     * 걸러내지 못하면 자기 자신이 피어 목록에 나타난다.
     */
    @Test
    fun `내 알림이 돌아와도 목록에 넣지 않는다`() = runBlocking {
        val port = freePort()
        val nodeId = "abcdef0123456789"
        val discovery = Discovery(
            nodeId = nodeId,
            name = "나",
            tcpPort = 9600,
            announcePort = port,
            listenPort = port,
            routes = { listOf(BroadcastRoute(loopback, listOf(loopback))) },
        )

        withScope { scope ->
            discovery.start(scope)
            awaitListening(discovery)

            // 알림 주기를 두 번 넘게 기다린다. 되받았다면 이 사이에 올라온다
            Thread.sleep(Config.ANNOUNCE_INTERVAL_MS * 2 + 500)
            assertEquals("자기 자신이 목록에 있다", emptyList<Peer>(), discovery.peers.value)

            // 남이 보낸 것은 잘 들어오는지 함께 확인한다. 안 그러면 소켓이 죽어서
            // 비어있는 것인지 제대로 걸러낸 것인지 구분되지 않는다
            send(Announce.encode("1111111111111111", "남", 9600, "linux"), port)
            val peers = withTimeout(5_000) { discovery.peers.first { it.isNotEmpty() } }
            assertEquals("남", peers[0].name)
        }
    }

    /** 내가 내보내는 알림을 밖에서 받아 읽을 수 있다. */
    @Test
    fun `내 알림을 실제 소켓으로 내보낸다`() = runBlocking {
        val receiver = DatagramSocket(null).apply {
            reuseAddress = true
            bind(InetSocketAddress(loopback, 0))
            soTimeout = 6_000
        }

        receiver.use {
            val discovery = Discovery(
                nodeId = "9999888877776666",
                name = "내 폰",
                tcpPort = 9607,
                announcePort = receiver.localPort,
                listenPort = freePort(), // 받는 쪽과 겹치지 않게
                routes = { listOf(BroadcastRoute(loopback, listOf(loopback))) },
            )

            withScope { scope ->
                discovery.start(scope)

                val packet = DatagramPacket(ByteArray(4096), 4096)
                receiver.receive(packet)

                val peer = Announce.parse(packet.data, packet.length, "127.0.0.1", 1L)
                assertNotNull("내가 보낸 알림을 해석할 수 없다", peer)
                assertEquals("9999888877776666", peer!!.nodeId)
                assertEquals("내 폰", peer.name)
                // TCP_PORT 를 그대로 쓰면 안 된다. 실제로 잡은 포트를 알려야 한다
                assertEquals(9607, peer.port)
                assertEquals("android", peer.platform)
            }
        }
    }

    @Test
    fun `포트를 못 잡으면 조용히 포기한다`() = runBlocking {
        // **와일드카드**로 잡아야 막힌다. 127.0.0.1 만 잡으면 0.0.0.0 재바인드가
        // 그냥 성공한다 (BSD 소켓 규칙). 처음에 이걸 틀려서 테스트가 실패했다
        val taken = DatagramSocket(null).apply {
            reuseAddress = false
            bind(InetSocketAddress(0))
        }

        taken.use {
            val discovery = Discovery(
                nodeId = "0000000000000001",
                name = "테스트",
                tcpPort = 9600,
                announcePort = freePort(),
                listenPort = taken.localPort,
                routes = { emptyList() },
            )

            withScope { scope ->
                discovery.start(scope)
                Thread.sleep(500)
                // 죽지 않고 듣기만 포기한다. 아는 상대에게 거는 연결은 계속 된다
                assertFalse(discovery.listening.value)
            }
        }
    }

    // --- 인터페이스 열거 ---

    @Test
    fun `열거한 경로는 모두 브로드캐스트 목적지를 갖는다`() {
        for (route in systemRoutes()) {
            assertFalse("루프백으로는 알릴 이유가 없다", route.source.isLoopbackAddress)
            assertTrue("목적지가 없다: ${route.source}", route.targets.isNotEmpty())
            assertTrue(
                "제한 브로드캐스트가 빠졌다: ${route.source}",
                route.targets.any { it.hostAddress == "255.255.255.255" },
            )
        }
    }

    // --- 도구 ---

    private fun peer(id: String, name: String, seen: Long) =
        Peer(id, name, "10.0.0.9", 9600, "linux", seen)

    private fun freePort() = DatagramSocket(0).use { it.localPort }

    private fun send(payload: ByteArray, port: Int) {
        DatagramSocket().use { it.send(DatagramPacket(payload, payload.size, loopback, port)) }
    }

    /** listener 가 실제로 포트를 잡을 때까지 기다린다. 안 기다리면 알림을 놓친다. */
    private suspend fun awaitListening(discovery: Discovery) {
        withTimeout(5_000) { discovery.listening.first { it } }
    }

    private inline fun withScope(body: (CoroutineScope) -> Unit) {
        val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
        try {
            body(scope)
        } finally {
            scope.cancel()
        }
    }

    private fun golden(name: String) =
        checkNotNull(javaClass.classLoader!!.getResourceAsStream("golden/$name")) {
            "golden/$name 이 없습니다. tools/gen-golden.py 를 실행하세요"
        }.use { it.readBytes() }
}
