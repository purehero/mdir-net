// 연결 수립 — hello 를 주고받고 사용자 승인을 받는다 (PROTOCOL.md 4절)

package com.purehero.netdir.net

import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketTimeoutException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import com.purehero.netdir.protocol.Config
import com.purehero.netdir.protocol.Frame
import com.purehero.netdir.protocol.FrameReader
import com.purehero.netdir.store.FileStore
import org.json.JSONObject

/**
 * 들어온 연결 요청 하나. 화면이 수락/거부를 정해주기를 기다린다.
 *
 * 30초가 지나면 거부로 처리하되, **화면에도 만료를 알려야 한다.** 알리지 않으면
 * 사용자가 늦게 수락을 눌렀을 때 창만 닫히고 아무 일도 없어 승인됐다고 믿게 된다.
 */
class ApprovalRequest(val name: String, val host: String, val platform: String) {

    private val decision = CompletableDeferred<Boolean>()

    fun decide(accepted: Boolean) {
        decision.complete(accepted)
    }

    suspend fun await(timeoutMs: Long = Config.APPROVAL_TIMEOUT_MS): Boolean =
        withTimeoutOrNull(timeoutMs) { decision.await() } ?: false
}

sealed interface ServerEvent {
    data class Approval(val request: ApprovalRequest) : ServerEvent
    data class ApprovalExpired(val request: ApprovalRequest) : ServerEvent
    data class Connected(val session: Session) : ServerEvent
}

/**
 * 상대에게 연결을 요청하고 승인을 기다린다.
 *
 * @param prepare 소켓을 Wi-Fi 네트워크에 묶는다. 셀룰러로 나가면 LAN 에 닿지 않는다.
 */
suspend fun connect(
    peer: Peer,
    myName: String,
    store: FileStore,
    scope: CoroutineScope,
    prepare: (Socket) -> Unit = {},
    onEvent: (SessionEvent) -> Unit = {},
): Session = withContext(Dispatchers.IO) {
    val socket = Socket()
    try {
        prepare(socket)
        socket.connect(InetSocketAddress(peer.host, peer.port), Config.CONNECT_TIMEOUT_MS)

        Frame.send(
            socket.getOutputStream(),
            JSONObject()
                .put("t", "hello")
                .put("v", Config.PROTOCOL_VERSION)
                .put("name", myName)
                .put("platform", Config.PLATFORM),
        )

        // 상대가 승인 버튼을 누를 시간을 준다. 29초째에 눌렀는데 이쪽이 이미
        // 포기해 있으면 안 되므로 여유를 더 둔다
        socket.soTimeout = Config.APPROVAL_WAIT_MS.toInt()
        val header = FrameReader(socket.getInputStream()).read().header

        if (header.optString("t") != "hello.ok") {
            val why = header.opt("msg") as? String
            throw Rejected(why ?: "${peer.name} 가 연결을 거부했습니다")
        }

        socket.soTimeout = 0
        Session(
            socket = socket,
            peerName = (header.opt("name") as? String)?.ifEmpty { null } ?: peer.name,
            peerHost = peer.host,
            store = store,
            scope = scope,
            onEvent = onEvent,
        ).also { it.start() }
    } catch (exc: SocketTimeoutException) {
        runCatching { socket.close() }
        throw SessionError("응답이 없습니다. 상대가 승인하지 않았습니다")
    } catch (exc: Throwable) {
        runCatching { socket.close() }
        throw exc
    }
}

/** 들어오는 연결을 받아 화면에 승인을 물어보는 TCP 서버. */
class Server(
    private val myName: () -> String,
    private val store: () -> FileStore,
    private val scope: CoroutineScope,
    private val basePort: Int = Config.TCP_PORT,
    /** 만료 경로를 30초 기다리지 않고 검사하려고 갈라뒀다. 실제로는 늘 기본값이다. */
    private val approvalTimeoutMs: Long = Config.APPROVAL_TIMEOUT_MS,
    private val onEvent: (ServerEvent) -> Unit,
    private val onSessionEvent: (SessionEvent) -> Unit = {},
) {
    private var server: ServerSocket? = null
    private var job: Job? = null

    /** 실제로 잡은 포트. **이 값을 브로드캐스트해야 한다.** */
    var port: Int = basePort
        private set

    /**
     * 포트를 잡는다. 이미 쓰는 중이면 하나씩 올려가며 PORT_TRIES 번까지 시도한다.
     *
     * 실제로 잡은 포트를 `port` 에 반영한다. `TCP_PORT` 를 그대로 브로드캐스트하면
     * 두 번째 인스턴스에 아무도 접속하지 못한다.
     */
    fun bind(): Int {
        for (offset in 0 until Config.PORT_TRIES) {
            val candidate = basePort + offset
            try {
                val socket = ServerSocket()
                socket.reuseAddress = true
                socket.bind(InetSocketAddress(candidate), 4)
                socket.soTimeout = 500 // 취소에 반응하려면 깨어나야 한다
                server = socket
                port = socket.localPort
                return port
            } catch (_: Exception) {
                continue
            }
        }
        throw SessionError("사용할 수 있는 포트를 찾지 못했습니다")
    }

    fun start() {
        job = scope.launch(Dispatchers.IO) { acceptLoop() }
    }

    fun stop() {
        job?.cancel()
        job = null
        runCatching { server?.close() }
        server = null
    }

    private suspend fun acceptLoop() {
        val socket = server ?: return
        while (coroutineIsActive()) {
            val client = try {
                socket.accept()
            } catch (_: SocketTimeoutException) {
                continue
            } catch (_: Exception) {
                break
            }
            scope.launch(Dispatchers.IO) { greet(client) }
        }
    }

    /** hello 를 읽고 화면에 승인을 물어본 뒤 결과를 회신한다. */
    private suspend fun greet(socket: Socket) {
        try {
            socket.soTimeout = Config.CONNECT_TIMEOUT_MS
            val header = FrameReader(socket.getInputStream()).read().header

            if (header.optString("t") != "hello" ||
                header.opt("v") != Config.PROTOCOL_VERSION
            ) {
                Frame.send(
                    socket.getOutputStream(),
                    JSONObject().put("t", "hello.no").put("msg", "버전이 맞지 않습니다"),
                )
                socket.close()
                return
            }

            val host = socket.inetAddress?.hostAddress ?: "?"
            val request = ApprovalRequest(
                name = (header.opt("name") as? String)?.ifEmpty { null } ?: host,
                host = host,
                platform = (header.opt("platform") as? String)?.ifEmpty { null } ?: "?",
            )
            onEvent(ServerEvent.Approval(request))

            if (!request.await(approvalTimeoutMs)) {
                // 알리지 않으면 늦게 수락을 눌렀을 때 창만 닫히고 아무 일도
                // 일어나지 않아 사용자는 승인됐다고 믿게 된다
                onEvent(ServerEvent.ApprovalExpired(request))
                Frame.send(socket.getOutputStream(), JSONObject().put("t", "hello.no"))
                socket.close()
                return
            }

            Frame.send(
                socket.getOutputStream(),
                JSONObject()
                    .put("t", "hello.ok")
                    .put("name", myName())
                    .put("platform", Config.PLATFORM),
            )
            socket.soTimeout = 0

            val session = Session(
                socket = socket,
                peerName = request.name,
                peerHost = host,
                store = store(),
                scope = scope,
                onEvent = onSessionEvent,
            )
            session.start()
            onEvent(ServerEvent.Connected(session))
        } catch (_: Exception) {
            runCatching { socket.close() }
        }
    }

    private suspend fun coroutineIsActive(): Boolean =
        kotlinx.coroutines.currentCoroutineContext().isActive
}
