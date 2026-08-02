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

package net.mdir.android.net

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import net.mdir.android.protocol.Config
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
