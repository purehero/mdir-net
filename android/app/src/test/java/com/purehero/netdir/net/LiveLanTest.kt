// 진짜 LAN 에서 파이썬 구현과 서로를 찾아내는지 — 자동 스위트에서는 빠진다
//
// 자동으로 돌리지 않는 이유는 네트워크 환경에 기대기 때문이다. 상대가 떠 있어야 하고,
// 공유기가 브로드캐스트를 막지 않아야 하고, 방화벽이 열려 있어야 한다. 조건이 없으면
// 실패가 아니라 그냥 확인할 수 없는 것이다. tests/manual_e2e.py 와 같은 자리다.
//
//   상대편(다른 터미널):  python3 tools/lan-probe.py --announce
//   이쪽:                 ./gradlew :app:testDebugUnitTest --tests '*LiveLanTest*' \
//                             -Dmdirnet.live=1 -i
//
//   반대 방향:            ./gradlew ... -Dmdirnet.live=1 -Dmdirnet.live.mode=announce
//                         python3 tools/lan-probe.py --listen
//
//   세션까지:             python3 tools/peer-probe.py --share <폴더> --accept
//                         ./gradlew ... -Dmdirnet.live=1 -Dmdirnet.live.mode=session \
//                             -Dmdirnet.live.dir=<받을폴더>

package com.purehero.netdir.net

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import com.purehero.netdir.protocol.Config
import com.purehero.netdir.store.LocalFileStore
import com.purehero.netdir.store.ShareList
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test

class LiveLanTest {

    private val live = System.getProperty("mdirnet.live") != null
    private val mode = System.getProperty("mdirnet.live.mode") ?: "listen"

    @Test
    fun `진짜 LAN 에서 상대를 찾는다`() = runBlocking {
        assumeTrue("-Dmdirnet.live=1 없이는 건너뛴다", live && mode == "listen")

        val discovery = Discovery(
            nodeId = newNodeId(),
            name = "안드로이드 탐침",
            tcpPort = Config.TCP_PORT,
        )

        report()
        withScope { scope ->
            discovery.start(scope)

            val listening = withTimeout(5_000) { discovery.listening.first { it } }
            assertTrue("UDP ${Config.UDP_PORT} 를 잡지 못했다", listening)
            println("듣는 중: UDP ${Config.UDP_PORT}")

            val peers = withTimeout(30_000) { discovery.peers.first { it.isNotEmpty() } }
            for (peer in peers) {
                println("찾았다: ${peer.name}  ${peer.host}:${peer.port}  (${peer.platform})")
            }
        }
    }

    @Test
    fun `진짜 LAN 으로 알린다`() = runBlocking {
        assumeTrue("-Dmdirnet.live.mode=announce 없이는 건너뛴다", live && mode == "announce")

        val discovery = Discovery(
            nodeId = newNodeId(),
            name = "안드로이드 탐침",
            tcpPort = Config.TCP_PORT,
        )

        report()
        println("id: ${discovery.nodeId}")
        withScope { scope ->
            discovery.start(scope)
            println("${Config.ANNOUNCE_INTERVAL_MS}ms 마다 알린다. 상대 목록을 확인하라")
            Thread.sleep(25_000)
        }
    }

    /**
     * 진짜 파이썬 구현과 세션을 맺고 목록·다운로드·업로드를 해본다.
     *
     * 골든 벡터는 바이트 모양만 본다. 이 테스트는 **살아있는 참조 구현**과
     * 핸드셰이크부터 전송까지 실제로 주고받는다. 상호운용의 최종 증거다.
     */
    @Test
    fun `진짜 파이썬 구현과 파일을 주고받는다`() = runBlocking {
        assumeTrue("-Dmdirnet.live.mode=session 없이는 건너뛴다", live && mode == "session")

        val workDir = java.io.File(
            System.getProperty("mdirnet.live.dir")
                ?: error("-Dmdirnet.live.dir=<받을폴더> 가 필요하다"),
        )
        workDir.mkdirs()

        val shares = ShareList().apply { add(workDir.absolutePath, "안드로이드") }
        val store = LocalFileStore(shares)

        // 올려보낼 것을 미리 만들어 둔다
        val outgoing = ByteArray(700_000) { ((it * 31) % 251).toByte() }
        java.io.File(workDir, "폰이보낸것.bin").writeBytes(outgoing)

        val discovery = Discovery(newNodeId(), "안드로이드 탐침", Config.TCP_PORT)
        report()

        withScope { scope ->
            discovery.start(scope)
            val peer = withTimeout(30_000) { discovery.peers.first { it.isNotEmpty() } }.first()
            println("찾았다: ${peer.name}  ${peer.host}:${peer.port}  (${peer.platform})")

            val session = withTimeout(40_000) {
                connect(peer, "안드로이드 탐침", store, scope)
            }
            println("연결됨: ${session.peerName}")

            try {
                val home = withTimeout(10_000) { session.remoteHome() }
                println("home  : $home")

                val (_, roots) = withTimeout(10_000) { session.remoteList(home) }
                println("공유  : ${roots.map { it.name }}")
                assertTrue("상대에게 공유가 없다", roots.isNotEmpty())

                val shareName = roots.first().name
                val (_, entries) = withTimeout(10_000) { session.remoteList(shareName) }
                println("목록  : ${entries.take(10).map { it.name }}")

                // 내려받기 — 상대의 첫 파일
                val file = entries.firstOrNull { !it.isDir }
                assertNotNull("상대 공유에 파일이 없다. --share 폴더에 파일을 넣어라", file)
                val got = withTimeout(120_000) {
                    session.pullFile("$shareName/${file!!.name}", "안드로이드/내려받음.bin")
                }
                println("내려받음: ${file!!.name}  ${got} 바이트")
                assertEquals(file.size, got)

                // 올려보내기
                val sent = withTimeout(120_000) {
                    session.pushFile("안드로이드/폰이보낸것.bin", "$shareName/폰이보낸것.bin")
                }
                println("올려보냄: $sent 바이트")
                assertEquals(outgoing.size.toLong(), sent)

                // 원격 폴더 만들기 / 이름 바꾸기 / 지우기
                withTimeout(10_000) { session.remoteMkdir("$shareName/폰이만든폴더") }
                withTimeout(10_000) { session.remoteRename("$shareName/폰이만든폴더", "이름바뀜") }
                withTimeout(10_000) { session.remoteDelete("$shareName/이름바뀜") }
                println("폴더 만들기/이름바꾸기/지우기 확인")

                println("상대 쪽에서 폰이보낸것.bin 의 해시를 확인하라")
            } finally {
                session.close()
            }
        }
    }

    /** 어떤 인터페이스로 나가는지 먼저 보여준다. 안 보이면 여기부터 의심한다. */
    private fun report() {
        val routes = systemRoutes()
        println("알림을 내보낼 경로 ${routes.size}개")
        for (route in routes) {
            val targets = route.targets.joinToString(", ") { it.hostAddress ?: "?" }
            println("  ${route.source.hostAddress} -> $targets")
        }
    }

    private inline fun withScope(body: (CoroutineScope) -> Unit) {
        val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
        try {
            body(scope)
        } finally {
            scope.cancel()
        }
    }
}
