// 앱 하나뿐인 MDirNet 노드 — 서버·탐색·세션·전송을 한 곳에서 들고 있다
//
// 서비스와 화면이 같은 것을 봐야 한다. 서비스는 프로세스를 살려두고 알림을 맡고,
// 화면은 상태를 그린다. 둘이 각자 상태를 들면 어긋나므로 여기 하나로 모은다.

package com.purehero.netdir.node

import android.content.Context
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import com.purehero.netdir.net.ApprovalRequest
import com.purehero.netdir.net.Discovery
import com.purehero.netdir.net.Peer
import com.purehero.netdir.net.Server
import com.purehero.netdir.net.ServerEvent
import com.purehero.netdir.net.Session
import com.purehero.netdir.net.SessionError
import com.purehero.netdir.net.SessionEvent
import com.purehero.netdir.net.WifiLan
import com.purehero.netdir.net.connect
import com.purehero.netdir.net.newNodeId
import com.purehero.netdir.protocol.Config
import com.purehero.netdir.store.FileStore
import com.purehero.netdir.store.SharesStore

/** 사용자에게 한 번 보여주고 사라지는 알림. */
data class Notice(val text: String, val stamp: Long)

object MDirNode {

    private var scope: CoroutineScope? = null
    private var wifi: WifiLan? = null
    private var server: Server? = null
    private var discovery: Discovery? = null

    lateinit var shares: SharesStore
        private set

    private val _running = MutableStateFlow(false)
    val running: StateFlow<Boolean> = _running.asStateFlow()

    private val _peers = MutableStateFlow<List<Peer>>(emptyList())
    val peers: StateFlow<List<Peer>> = _peers.asStateFlow()

    private val _session = MutableStateFlow<Session?>(null)
    val session: StateFlow<Session?> = _session.asStateFlow()

    private val _approval = MutableStateFlow<ApprovalRequest?>(null)
    val approval: StateFlow<ApprovalRequest?> = _approval.asStateFlow()

    private val _notice = MutableStateFlow<Notice?>(null)
    val notice: StateFlow<Notice?> = _notice.asStateFlow()

    private val _port = MutableStateFlow(Config.TCP_PORT)
    val port: StateFlow<Int> = _port.asStateFlow()

    /** 이 기기의 지문. 켤 때 정해지고 끄면 사라진다 (PROTOCOL.md 2.5절). */
    private val _fingerprint = MutableStateFlow<String?>(null)
    val fingerprint: StateFlow<String?> = _fingerprint.asStateFlow()

    private val _online = MutableStateFlow(false)
    val online: StateFlow<Boolean> = _online.asStateFlow()

    val transfers = Transfers()

    /** 화면이 앞에 있는가. 승인을 다이얼로그로 물을지 알림으로 물을지 가른다. */
    @Volatile
    var uiVisible: Boolean = false

    /** 승인을 알림으로 물어야 할 때 서비스가 채운다. */
    var onApprovalNeeded: ((ApprovalRequest) -> Unit)? = null
    var onApprovalSettled: (() -> Unit)? = null

    private var deviceName: String = "안드로이드"

    // --- 생명주기 ---

    /**
     * 공유 목록을 읽어둔다. 서비스가 꺼져 있어도 공유를 고칠 수 있어야 하므로
     * 시작과 갈라놨다.
     */
    fun ensureShares(context: Context) {
        if (!::shares.isInitialized) shares = SharesStore(context.applicationContext)
    }

    fun start(context: Context, name: String) {
        if (_running.value) return

        deviceName = name
        val app = context.applicationContext
        ensureShares(app)

        val work = CoroutineScope(Dispatchers.IO + SupervisorJob())
        scope = work

        val lan = WifiLan(app).also { it.acquire() }
        wifi = lan
        work.launch { lan.online.collect { _online.value = it } }

        val tcp = Server(
            myName = { deviceName },
            store = { store() },
            localStore = { localStore() ?: store() },
            scope = work,
            onEvent = ::onServerEvent,
            onSessionEvent = ::onSessionEvent,
        )
        _port.value = try {
            tcp.bind()
        } catch (exc: SessionError) {
            notify(exc.message ?: "포트를 잡지 못했습니다")
            wifi?.release()
            scope = null
            return
        }
        tcp.start()
        server = tcp
        _fingerprint.value = tcp.fingerprint()

        // **실제로 잡은 포트**를 알린다. TCP_PORT 를 그대로 쓰면 아무도 못 붙는다
        val disco = Discovery(
            nodeId = newNodeId(),
            name = name,
            tcpPort = tcp.port,
            prepare = { lan.bind(it) },
        )
        disco.start(work)
        discovery = disco
        work.launch { disco.peers.collect { _peers.value = it } }

        _running.value = true
    }

    fun stop() {
        _session.value?.close("내가 껐습니다")
        _session.value = null
        _approval.value?.decide(false)
        _approval.value = null

        discovery?.stop()
        server?.stop()
        wifi?.release()
        scope?.cancel()

        discovery = null
        server = null
        wifi = null
        scope = null

        _peers.value = emptyList()
        _running.value = false
        _online.value = false
        // 인증서를 지웠으니 지문도 남기지 않는다. 남기면 다음에 켰을 때 옛 값을
        // 보고 대조하게 된다
        _fingerprint.value = null
    }

    fun rename(name: String) {
        deviceName = name
        discovery?.name = name
    }

    fun store(): FileStore = shares.store()

    /** 브라우저의 `내 폰` 쪽. 작업 폴더가 정해지기 전에는 없다. */
    fun localStore(): FileStore? = shares.localStore()

    // --- 연결 ---

    fun connectTo(peer: Peer) {
        val work = scope ?: return
        if (_session.value != null) {
            notify("이미 ${_session.value?.peerName} 와 연결돼 있습니다")
            return
        }

        work.launch {
            try {
                val live = connect(
                    peer = peer,
                    myName = deviceName,
                    store = store(),
                    localStore = localStore() ?: store(),
                    scope = work,
                    prepare = { wifi?.bind(it) },
                    onEvent = ::onSessionEvent,
                )
                _session.value = live
                notify("${live.peerName} 와 연결됐습니다")
            } catch (exc: Exception) {
                notify(exc.message ?: "연결하지 못했습니다")
            }
        }
    }

    fun disconnect() {
        _session.value?.close("연결을 끊었습니다")
        _session.value = null
    }

    fun decideApproval(accepted: Boolean) {
        _approval.value?.decide(accepted)
        _approval.value = null
        onApprovalSettled?.invoke()
    }

    // --- 이벤트 ---

    private fun onServerEvent(event: ServerEvent) {
        when (event) {
            is ServerEvent.Approval -> {
                if (_session.value != null) {
                    // 한 번에 한 연결만 다룬다. 두 번째는 조용히 거절한다
                    event.request.decide(false)
                    return
                }
                _approval.value = event.request
                // 앱이 앞에 없으면 다이얼로그를 볼 수 없다. 알림으로 물어야 한다
                if (!uiVisible) onApprovalNeeded?.invoke(event.request)
            }

            is ServerEvent.ApprovalExpired -> {
                // 알리지 않으면 늦게 수락을 눌렀을 때 창만 닫히고 아무 일도
                // 일어나지 않아 승인됐다고 믿게 된다
                if (_approval.value === event.request) _approval.value = null
                onApprovalSettled?.invoke()
                notify("${event.request.name} 의 연결 요청이 시간이 지나 취소됐습니다")
            }

            is ServerEvent.Connected -> {
                _session.value = event.session
                _approval.value = null
                onApprovalSettled?.invoke()
                notify("${event.session.peerName} 가 연결했습니다")
            }
        }
    }

    private fun onSessionEvent(event: SessionEvent) {
        when (event) {
            is SessionEvent.Disconnected -> {
                _session.value = null
                notify(event.reason)
            }
            is SessionEvent.Received -> notify("받았습니다: ${event.remotePath}")
            is SessionEvent.Changed -> Unit // 화면이 알아서 새로 그린다
        }
    }

    fun notify(text: String) {
        _notice.value = Notice(text, System.currentTimeMillis())
    }

    fun clearNotice() {
        _notice.value = null
    }
}
