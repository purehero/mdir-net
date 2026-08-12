// UDP 브로드캐스트로 같은 공유기 안의 다른 MDirNet 노드를 찾아낸다 (PROTOCOL.md 2절)
//
// 안드로이드 클래스를 쓰지 않는다. MulticastLock 과 Wi-Fi 바인딩은 훅으로 주입받아
// WifiLan 이 채운다. 그래야 이 파일이 JVM 단위 테스트에서 실제 소켓으로 검증된다.

package com.purehero.netdir.net

import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.Inet4Address
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.NetworkInterface
import java.net.SocketTimeoutException
import java.security.SecureRandom
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import com.purehero.netdir.protocol.Config
import org.json.JSONException
import org.json.JSONObject
import org.json.JSONTokener

/** 발견된 상대 노드 하나. */
data class Peer(
    val nodeId: String,
    val name: String,
    val host: String,
    val port: Int,
    val platform: String,
    val lastSeenMs: Long,
) {
    val label: String get() = "$name  ($host:$port)"
}

/** 프로세스마다 새로 만드는 노드 식별자. 자기 패킷을 걸러내는 데만 쓴다. */
fun newNodeId(): String {
    val raw = ByteArray(8)
    SecureRandom().nextBytes(raw)
    return raw.joinToString("") { "%02x".format(it) }
}

/**
 * 알림 데이터그램. 프레이밍이 없다 — 본문 전체가 JSON 객체 하나다.
 *
 * 파이썬 쪽은 기본 옵션 `json.dumps` 라 한글이 `\uXXXX` 로 escape 되고 구분자에
 * 공백이 들어간다. 프레임 헤더와 규칙이 다르지만 파서를 쓰면 저절로 해결된다.
 */
object Announce {

    fun encode(nodeId: String, name: String, tcpPort: Int, platform: String): ByteArray =
        JSONObject()
            .put("v", Config.PROTOCOL_VERSION)
            .put("id", nodeId)
            .put("name", name)
            .put("port", tcpPort)
            .put("platform", platform)
            .toString()
            .toByteArray(Charsets.UTF_8)

    /**
     * 받은 데이터그램을 Peer 로. 규격에 맞지 않으면 null 을 돌려 **조용히 버린다.**
     *
     * host 는 페이로드가 아니라 **출발지 IP** 다. 알림에는 주소 필드가 없다.
     */
    fun parse(data: ByteArray, length: Int, host: String, nowMs: Long): Peer? {
        val text = try {
            String(data, 0, length, Charsets.UTF_8)
        } catch (_: Exception) {
            return null
        }

        val msg = parseWholeObject(text) ?: return null

        // optInt 는 문자열 "1" 을 숫자로 바꿔 받아준다. 파이썬은 정수 1 과의 엄격
        // 비교라 거부하므로 여기서도 타입을 본다
        if (msg.opt("v") != Config.PROTOCOL_VERSION) return null

        val nodeId = msg.opt("id") as? String ?: return null
        // 정수여야 한다. "9600" 같은 문자열을 받아주면 엉뚱한 포트로 붙는다
        val port = msg.opt("port")
        if (port !is Int) return null

        return Peer(
            nodeId = nodeId,
            name = (msg.opt("name") as? String)?.takeIf { it.isNotEmpty() } ?: host,
            host = host,
            port = port,
            platform = (msg.opt("platform") as? String)?.takeIf { it.isNotEmpty() } ?: "?",
            lastSeenMs = nowMs,
        )
    }

    /**
     * 본문 **전체**가 JSON 객체 하나일 때만 돌려준다. 아니면 null.
     *
     * `JSONObject(text)` 는 첫 객체만 읽고 뒤에 남은 것을 조용히 버린다. 그래서
     * `{"v":1,...}쓰레기` 가 통과한다. 파이썬 `json.loads` 는 "Extra data" 로 거부하므로
     * 여기서도 거부해야 한다. 뒤쪽 공백만 허용하는 것도 파이썬과 같다.
     */
    private fun parseWholeObject(text: String): JSONObject? {
        val trimmed = text.trim()
        return try {
            val tokener = JSONTokener(trimmed)
            val value = tokener.nextValue()
            if (value !is JSONObject || tokener.more()) null else value
        } catch (_: JSONException) {
            null
        }
    }
}

/** 살아있는 피어 목록. TTL 이 지난 항목은 지운다. */
class PeerTable(private val ttlMs: Long = Config.PEER_TTL_MS) {

    private val peers = LinkedHashMap<String, Peer>()

    /** 알림 하나를 반영한다. 목록이 달라졌으면 true. */
    @Synchronized
    fun observe(peer: Peer): Boolean {
        val old = peers.put(peer.nodeId, peer)
        // 마지막으로 본 시각만 바뀐 것은 화면에 알릴 일이 아니다
        return old == null ||
            old.name != peer.name ||
            old.host != peer.host ||
            old.port != peer.port ||
            old.platform != peer.platform
    }

    /** 소식이 끊긴 항목을 지운다. 지운 게 있으면 true. */
    @Synchronized
    fun prune(nowMs: Long): Boolean =
        peers.values.removeAll { nowMs - it.lastSeenMs > ttlMs }

    @Synchronized
    fun snapshot(): List<Peer> = peers.values.sortedBy { it.name.lowercase() }

    @Synchronized
    fun clear() = peers.clear()
}

/** 알림을 내보낼 인터페이스 하나와 그 목적지들. */
data class BroadcastRoute(val source: InetAddress, val targets: List<InetAddress>)

/**
 * 알림을 내보낼 경로들.
 *
 * `sendto("255.255.255.255")` 한 번은 라우팅 테이블이 고른 **인터페이스 하나로만**
 * 나간다. 그래서 인터페이스마다 소켓을 따로 묶어 보내야 한다.
 *
 * 파이썬은 표준 라이브러리로 넷마스크를 알 수 없어 `/24` 를 가정하지만, JVM 은
 * `getInterfaceAddresses()` 가 **실제 브로드캐스트 주소**를 준다. 추측하지 않는다.
 */
fun systemRoutes(): List<BroadcastRoute> {
    val routes = mutableListOf<BroadcastRoute>()
    val limited = InetAddress.getByName("255.255.255.255")

    val interfaces = try {
        NetworkInterface.getNetworkInterfaces()?.toList().orEmpty()
    } catch (_: Exception) {
        return emptyList()
    }

    for (iface in interfaces) {
        val usable = try {
            iface.isUp && !iface.isLoopback
        } catch (_: Exception) {
            false
        }
        if (!usable) continue

        for (entry in iface.interfaceAddresses) {
            val source = entry.address as? Inet4Address ?: continue
            // 브로드캐스트 주소가 없는 주소(포인트투포인트 등)로는 알릴 수 없다
            val subnet = entry.broadcast ?: continue
            routes.add(BroadcastRoute(source, listOf(limited, subnet)))
        }
    }
    return routes
}

/**
 * 자기 존재를 알리는 announcer 와 남의 소식을 듣는 listener 를 함께 돌린다.
 *
 * @param prepare 소켓을 쓰기 전에 불린다. 안드로이드에서는 Wi-Fi 네트워크에 묶는다.
 *                셀룰러가 같이 켜져 있으면 자바 소켓이 그쪽으로 나가 LAN 에 닿지 않는다.
 */
class Discovery(
    val nodeId: String,
    name: String,
    @Volatile var tcpPort: Int,
    /**
     * 알림을 보내는 포트와 듣는 포트. 실제로는 늘 같은 값(9601)이다.
     *
     * 갈라둔 이유는 테스트다. 한 JVM 에서 보내는 쪽과 받는 쪽을 함께 세우려면
     * 포트가 달라야 한다. 같은 포트에 소켓 둘을 묶으려면 SO_REUSEPORT 가 필요한데
     * `DatagramSocket.setOption` 은 API 33 부터라 minSdk 26 에서는 쓸 수 없다.
     */
    private val announcePort: Int = Config.UDP_PORT,
    private val listenPort: Int = announcePort,
    private val platform: String = Config.PLATFORM,
    private val routes: () -> List<BroadcastRoute> = ::systemRoutes,
    private val prepare: (DatagramSocket) -> Unit = {},
    private val nowMs: () -> Long = { System.nanoTime() / 1_000_000 },
) {
    @Volatile
    var name: String = name

    private val table = PeerTable()
    private val _peers = MutableStateFlow<List<Peer>>(emptyList())
    val peers: StateFlow<List<Peer>> = _peers.asStateFlow()

    /** 수신 포트를 잡지 못했는지. 탐색만 포기하고 직접 연결은 계속 된다. */
    private val _listening = MutableStateFlow(false)
    val listening: StateFlow<Boolean> = _listening.asStateFlow()

    private var jobs = listOf<Job>()

    fun start(scope: CoroutineScope) {
        jobs = listOf(
            scope.launch(Dispatchers.IO) { announceLoop() },
            scope.launch(Dispatchers.IO) { listenLoop() },
        )
    }

    fun stop() {
        jobs.forEach { it.cancel() }
        jobs = emptyList()
        table.clear()
        _peers.value = emptyList()
    }

    // --- 알리는 쪽 ---

    private suspend fun announceLoop() {
        while (currentScopeActive()) {
            announceOnce()
            delay(Config.ANNOUNCE_INTERVAL_MS)
        }
    }

    private fun announceOnce() {
        val payload = Announce.encode(nodeId, name, tcpPort, platform)

        // 주기마다 주소를 다시 알아본다. Wi-Fi 를 다시 붙거나 VPN 을 켜면 주소가
        // 바뀌는데, 시작할 때 한 번만 잡아두면 그때부터 아무에게도 안 보인다
        val found = routes()
        var sent = false
        for (route in found) {
            sent = announceFrom(payload, route) || sent
        }

        if (!sent) {
            // 주소를 못 알아냈거나 전부 실패했다. 인터페이스를 지정하지 않고 한 번은
            // 보낸다. 인터페이스가 하나인 기기는 이것으로 충분하다
            announceFrom(payload, null)
        }
    }

    /** 이 인터페이스에 소켓을 묶어 보낸다. 하나라도 나갔으면 true. */
    private fun announceFrom(payload: ByteArray, route: BroadcastRoute?): Boolean {
        val socket = try {
            // 묶기 전에 prepare 를 부를 수 있도록 묶이지 않은 소켓으로 만든다.
            // Network.bindSocket 은 이미 묶인 소켓에 대해 실패할 수 있다
            DatagramSocket(null).apply {
                broadcast = true
                bindToNetwork(this)
                bind(if (route == null) InetSocketAddress(0) else InetSocketAddress(route.source, 0))
            }
        } catch (_: Exception) {
            return false // 방금 내려간 인터페이스. 다음 주기에 다시 본다
        }

        return socket.use { sock ->
            val targets = route?.targets ?: listOf(InetAddress.getByName("255.255.255.255"))
            var ok = false
            for (target in targets) {
                try {
                    sock.send(DatagramPacket(payload, payload.size, target, announcePort))
                    ok = true
                } catch (_: Exception) {
                    // 이 목적지만 실패. 다른 목적지를 계속 시도한다
                }
            }
            ok
        }
    }

    // --- 듣는 쪽 ---

    private suspend fun listenLoop() {
        val socket = try {
            DatagramSocket(null).apply {
                reuseAddress = true
                bindToNetwork(this)
                bind(InetSocketAddress(listenPort))
                // 취소에 반응하고, 조용할 때 TTL 을 걷어내려면 깨어나야 한다
                soTimeout = 500
            }
        } catch (_: Exception) {
            // 포트를 못 잡으면 탐색만 조용히 포기한다. 직접 연결은 계속 된다
            _listening.value = false
            return
        }

        _listening.value = true
        val buffer = ByteArray(4096)

        try {
            while (currentScopeActive()) {
                var changed = false
                try {
                    val packet = DatagramPacket(buffer, buffer.size)
                    socket.receive(packet)
                    changed = accept(packet)
                } catch (_: SocketTimeoutException) {
                    // 조용한 주기. 아래에서 TTL 만 걷어낸다
                } catch (_: Exception) {
                    break
                }

                if (table.prune(nowMs()) || changed) {
                    _peers.value = table.snapshot()
                }
            }
        } finally {
            _listening.value = false
            socket.close()
        }
    }

    /** 데이터그램 하나를 반영한다. 목록이 달라졌으면 true. */
    private fun accept(packet: DatagramPacket): Boolean {
        val host = packet.address?.hostAddress ?: return false
        val peer = Announce.parse(packet.data, packet.length, host, nowMs()) ?: return false
        // 내 브로드캐스트가 나에게 돌아온 것
        if (peer.nodeId == nodeId) return false
        return table.observe(peer)
    }

    /**
     * Wi-Fi 네트워크에 묶는다. 실패해도 계속 간다.
     *
     * 묶지 못하면 기본 경로(셀룰러일 수 있다)로 나가 LAN 에 안 닿을 뿐이다.
     * 여기서 포기해 버리면 Wi-Fi 만 켜진 평범한 상황까지 함께 죽는다.
     */
    private fun bindToNetwork(socket: DatagramSocket) {
        try {
            prepare(socket)
        } catch (_: Exception) {
        }
    }

    /** 소켓 타임아웃이 500ms 라 취소는 그 안에 반영된다. 예외를 던지지 않는다. */
    private suspend fun currentScopeActive(): Boolean =
        kotlinx.coroutines.currentCoroutineContext().isActive
}
