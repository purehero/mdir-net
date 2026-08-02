// 수립된 연결 하나 — 양쪽이 서로에게 요청하는 대칭 구조 (PROTOCOL.md 5절)
//
// 이 파일에서 제일 조심할 것은 `q` 규칙이다. 양쪽이 각자 1번부터 요청 id 를 매기므로
// id 만으로는 어느 대화인지 알 수 없다. 대화를 **시작한 쪽**이 보내는 프레임에만
// `q` 를 붙이고, 우편함을 두 개로 나눠 섞이지 않게 한다. 이 규칙이 없으면 내 pull
// 응답과 상대의 push 데이터가 같은 우편함으로 들어간다.

package net.mdir.android.net

import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.Socket
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import net.mdir.android.protocol.Config
import net.mdir.android.protocol.Frame
import net.mdir.android.protocol.FrameReader
import net.mdir.android.protocol.Incoming
import net.mdir.android.protocol.PeerClosed
import net.mdir.android.protocol.ProtocolError
import net.mdir.android.store.Entry
import net.mdir.android.store.FileStore
import net.mdir.android.store.Head
import net.mdir.android.store.PathDenied
import net.mdir.android.store.RemotePath
import net.mdir.android.store.Stat
import net.mdir.android.store.Tree
import net.mdir.android.store.TreeFile
import org.json.JSONArray
import org.json.JSONObject

/** 세션 수준의 실패. 사용자에게 문자열로 보여줄 수 있는 오류. */
open class SessionError(message: String) : Exception(message)

/** 상대가 연결 요청을 거부했다. */
class Rejected(message: String) : SessionError(message)

/** 세션이 바깥에 알리는 일들. */
sealed interface SessionEvent {
    data class Disconnected(val reason: String) : SessionEvent

    /** 상대가 내 공유 폴더에 파일을 밀어넣었다. */
    data class Received(val remotePath: String) : SessionEvent

    /** 상대가 내 공유 폴더를 바꿨다. 화면을 새로 그려야 한다. */
    data class Changed(val remotePath: String) : SessionEvent
}

/** 상대가 시작한 대화의 첫 프레임으로 인정되는 타입 (PROTOCOL.md 5.3). */
private val REQUEST_TYPES = setOf(
    "home", "list", "stat", "tree", "head", "mkdir", "delete", "rename", "pull", "push.begin",
)

class Session(
    private val socket: Socket,
    val peerName: String,
    val peerHost: String,
    private val store: FileStore,
    private val scope: CoroutineScope,
    private val onEvent: (SessionEvent) -> Unit,
) {
    private val input: InputStream = socket.getInputStream()
    private val output: OutputStream = socket.getOutputStream()
    private val reader = FrameReader(input)

    // 여러 코루틴이 같은 소켓에 쓴다. 프레임 하나는 통째로 나가야 한다
    private val sendLock = Mutex()

    private val ids = AtomicInteger(0)

    // 내가 시작한 대화의 응답 / 상대가 시작한 대화의 후속 프레임
    private val outgoing = HashMap<Int, Channel<Incoming>>()
    private val incoming = HashMap<Int, Channel<Incoming>>()
    private val boxLock = Any()

    @Volatile
    private var closed = false
    private var readerJob: Job? = null

    val isClosed: Boolean get() = closed

    // --- 생명주기 ---

    fun start() {
        readerJob = scope.launch(Dispatchers.IO) { readLoop() }
    }

    /** 소켓을 닫고 기다리던 모든 요청을 깨운다. 두 번 불러도 안전하다. */
    fun close(reason: String? = null) {
        synchronized(boxLock) {
            if (closed) return
            closed = true
        }

        // 이미 끊긴 뒤라면 인사를 못 보내도 상관없다
        runCatching { Frame.send(output, JSONObject().put("t", "bye")) }
        runCatching { socket.close() }

        // 깨우지 않으면 워커들이 30초 타임아웃까지 매달려 있는다
        val boxes = synchronized(boxLock) {
            val all = outgoing.values + incoming.values
            outgoing.clear()
            incoming.clear()
            all
        }
        for (box in boxes) box.close(SessionError("연결이 끊어졌습니다"))

        readerJob?.cancel()
        onEvent(SessionEvent.Disconnected(reason ?: "연결이 끊어졌습니다"))
    }

    // --- 저수준 송수신 ---

    /**
     * 프레임 하나를 내보낸다.
     *
     * **반드시 IO 디스패처로 옮긴다.** 소켓 쓰기는 블로킹이고, 안드로이드는 메인
     * 스레드에서 그러면 `NetworkOnMainThreadException` 을 던진다. 그 예외는 메시지가
     * 없어서 화면에는 그냥 "읽지 못했습니다" 로만 보인다 — 실기에서 이걸로 한참
     * 헤맸다. 호출하는 쪽이 조심하게 두지 않고, 소켓을 가진 여기서 보장한다.
     */
    private suspend fun send(header: JSONObject, blob: ByteArray? = null) {
        sendLock.withLock {
            withContext(Dispatchers.IO) {
                try {
                    Frame.send(output, header, blob)
                } catch (exc: IOException) {
                    // 닫힌 소켓에 쓰면 IOException 이 난다. 그대로 올려보내면 워커에서
                    // 터져 나오므로 호출하는 쪽이 한 종류만 잡도록 바꿔준다
                    throw SessionError("연결이 끊어졌습니다: ${exc.message}")
                }
            }
        }
    }

    /** 내가 시작한 대화의 프레임. `q` 를 붙여 상대가 구분할 수 있게 한다. */
    private suspend fun sendQ(header: JSONObject, blob: ByteArray? = null) =
        send(header.put("q", 1), blob)

    private suspend fun readLoop() {
        var reason: String? = null
        try {
            while (!closed) {
                val frame = readFrame()
                val kind = frame.header.optString("t")

                if (kind == "bye") {
                    reason = "$peerName 가 연결을 종료했습니다"
                    break
                }

                if (frame.header.has("q")) {
                    routePeerInitiated(kind, frame)
                } else {
                    route(outgoing, frame)
                }
            }
        } catch (_: PeerClosed) {
            reason = "$peerName 와의 연결이 끊어졌습니다"
        } catch (exc: ProtocolError) {
            reason = "프로토콜 오류: ${exc.message}"
        } catch (exc: IOException) {
            reason = "네트워크 오류: ${exc.message}"
        } finally {
            close(reason)
        }
    }

    /** 블로킹 읽기를 IO 디스패처에 둔다. 취소는 소켓을 닫는 것으로 이뤄진다. */
    private fun readFrame(): Incoming = reader.read()

    private fun routePeerInitiated(kind: String, frame: Incoming) {
        if (kind !in REQUEST_TYPES) {
            route(incoming, frame)
            return
        }

        val id = frame.header.optInt("id", -1)
        val box = Channel<Incoming>(Channel.UNLIMITED)

        // 우편함 등록은 **이 코루틴에서** 해야 한다. 처리 쪽에 맡기면 곧바로
        // 뒤따라오는 data 프레임이 등록보다 먼저 도착해 버려진다
        synchronized(boxLock) {
            if (closed) return
            incoming[id] = box
        }

        scope.launch(Dispatchers.IO) { handleRequest(kind, id, frame, box) }
    }

    private fun route(table: HashMap<Int, Channel<Incoming>>, frame: Incoming) {
        val box = synchronized(boxLock) { table[frame.header.optInt("id", -1)] }
        box?.trySend(frame)
    }

    // --- 요청하는 쪽 ---

    private fun openBox(): Pair<Int, Channel<Incoming>> {
        val id = ids.incrementAndGet()
        val box = Channel<Incoming>(Channel.UNLIMITED)
        synchronized(boxLock) {
            if (closed) throw SessionError("연결이 끊어졌습니다")
            outgoing[id] = box
        }
        return id to box
    }

    private fun closeBox(table: HashMap<Int, Channel<Incoming>>, id: Int) {
        synchronized(boxLock) { table.remove(id) }?.close()
    }

    /** 우편함에서 프레임 하나를 꺼낸다. 오류 응답은 예외로 바꿔 올린다. */
    private suspend fun await(
        box: Channel<Incoming>,
        timeoutMs: Long = Config.REQUEST_TIMEOUT_MS,
    ): Incoming {
        val frame = withTimeoutOrNull(timeoutMs) {
            try {
                box.receive()
            } catch (exc: Exception) {
                throw (exc as? SessionError ?: SessionError("연결이 끊어졌습니다"))
            }
        } ?: throw SessionError("응답이 없습니다 (${timeoutMs / 1000}초 초과)")

        if (frame.header.optString("t") == "err") {
            throw SessionError(frame.header.text("msg") ?: "알 수 없는 오류")
        }
        return frame
    }

    /** 단일 응답을 받는 평범한 요청. */
    private suspend fun request(kind: String, build: JSONObject.() -> Unit = {}): JSONObject {
        val (id, box) = openBox()
        try {
            sendQ(JSONObject().put("t", kind).put("id", id).apply(build))
            return await(box).header
        } finally {
            closeBox(outgoing, id)
        }
    }

    suspend fun remoteHome(): String = request("home").text("path") ?: RemotePath.ROOT

    suspend fun remoteList(path: String): Pair<String, List<Entry>> {
        val reply = request("list") { put("path", path) }
        return (reply.text("path") ?: path) to reply.entries()
    }

    suspend fun remoteStat(path: String): Stat {
        val reply = request("stat") { put("path", path) }
        return Stat(
            exists = reply.optBoolean("exists"),
            isDir = reply.optBoolean("is_dir"),
            size = reply.optLong("size"),
        )
    }

    suspend fun remoteTree(path: String): Tree {
        val reply = request("tree") { put("path", path) }
        return Tree(reply.treeFiles(), reply.strings("dirs"))
    }

    suspend fun remoteHead(path: String, limit: Int): Head {
        val (id, box) = openBox()
        try {
            sendQ(JSONObject().put("t", "head").put("id", id).put("path", path).put("limit", limit))
            val frame = await(box)
            return Head(frame.header.optLong("size"), frame.blob ?: ByteArray(0))
        } finally {
            closeBox(outgoing, id)
        }
    }

    suspend fun remoteMkdir(path: String) {
        request("mkdir") { put("path", path) }
    }

    suspend fun remoteDelete(path: String) {
        request("delete") { put("path", path) }
    }

    suspend fun remoteRename(path: String, newName: String) {
        request("rename") { put("path", path).put("name", newName) }
    }

    // --- 전송: 상대 > 나 ---

    /**
     * 상대 파일 하나를 내 공유 폴더로 받는다.
     *
     * 받는 자리도 원격 경로 공간이다. 폰은 SAF 트리 밖에 쓸 수 없으므로 호스트
     * 경로를 받을 이유가 없다.
     */
    suspend fun pullFile(
        remotePath: String,
        destPath: String,
        onProgress: ((Int) -> Unit)? = null,
    ): Long {
        val (id, box) = openBox()
        // 저장소 접근도 블로킹이다. 메인에서 부르면 SAF 쿼리가 ANR 로 이어진다
        val writer = withContext(Dispatchers.IO) { store.beginWrite(destPath) }
        var received = 0L

        try {
            sendQ(JSONObject().put("t", "pull").put("id", id).put("path", remotePath))
            while (true) {
                val frame = await(box)
                if (frame.header.optString("t") == "data.end") break
                frame.blob?.let {
                    withContext(Dispatchers.IO) { writer.write(it, it.size) }
                    received += it.size
                    onProgress?.invoke(it.size)
                }
            }
            withContext(Dispatchers.IO) { writer.commit() }
            return received
        } catch (exc: Throwable) {
            withContext(NonCancellable + Dispatchers.IO) { writer.abort() }
            throw exc
        } finally {
            closeBox(outgoing, id)
        }
    }

    // --- 전송: 나 > 상대 ---

    suspend fun pushFile(
        sourcePath: String,
        remotePath: String,
        onProgress: ((Int) -> Unit)? = null,
    ): Long {
        val size = withContext(Dispatchers.IO) { store.stat(sourcePath).size }
        val (id, box) = openBox()

        try {
            sendQ(
                JSONObject().put("t", "push.begin").put("id", id)
                    .put("path", remotePath).put("size", size),
            )
            await(box) // push.ok. 경로가 거부되면 여기서 예외가 난다

            var sent = 0L
            withContext(Dispatchers.IO) { store.openRead(sourcePath) }.use { source ->
                val buffer = ByteArray(Config.CHUNK_SIZE)
                while (true) {
                    val got = withContext(Dispatchers.IO) { source.read(buffer) }
                    if (got <= 0) break
                    // 대화를 시작한 쪽이 계속 보내는 것이므로 q 가 붙는다
                    sendQ(JSONObject().put("t", "data").put("id", id), buffer.copyOf(got))
                    sent += got
                    onProgress?.invoke(got)
                }
            }

            sendQ(JSONObject().put("t", "data.end").put("id", id))
            await(box) // push.done
            return sent
        } finally {
            closeBox(outgoing, id)
        }
    }

    // --- 응답하는 쪽 ---

    private suspend fun handleRequest(
        kind: String,
        id: Int,
        frame: Incoming,
        box: Channel<Incoming>,
    ) {
        try {
            when (kind) {
                "home" -> onHome(id)
                "list" -> onList(id, frame.header)
                "stat" -> onStat(id, frame.header)
                "tree" -> onTree(id, frame.header)
                "head" -> onHead(id, frame.header)
                "mkdir" -> onMkdir(id, frame.header)
                "delete" -> onDelete(id, frame.header)
                "rename" -> onRename(id, frame.header)
                "pull" -> onPull(id, frame.header)
                "push.begin" -> onPushBegin(id, frame.header, box)
            }
        } catch (exc: ProtocolError) {
            close("프로토콜 오류: ${exc.message}")
        } catch (exc: Throwable) {
            // 어떤 예외가 나더라도 **반드시 답을 보낸다.** 답이 없으면 상대는 요청
            // 타임아웃(30초)까지 그대로 멈춰 있게 된다. 예상 못한 버그가 상대를
            // 얼어붙게 만드는 것이 조용히 실패하는 것보다 훨씬 나쁘다
            replyError(id, "${exc.javaClass.simpleName}: ${exc.message}")
        } finally {
            closeBox(incoming, id)
        }
    }

    private suspend fun replyError(id: Int, message: String) {
        // 오류를 알려주려는 순간 연결이 끊겼다면 더 할 수 있는 게 없다
        runCatching { send(JSONObject().put("t", "err").put("id", id).put("msg", message)) }
    }

    private suspend fun reply(id: Int, kind: String, build: JSONObject.() -> Unit = {}) =
        send(JSONObject().put("t", kind).put("id", id).apply(build))

    private suspend fun onHome(id: Int) {
        // 상대는 늘 가상 루트에서 시작한다. 내 실제 경로는 알려주지 않는다
        reply(id, "home.ok") { put("path", RemotePath.ROOT) }
    }

    private suspend fun onList(id: Int, header: JSONObject) {
        val path = header.text("path") ?: RemotePath.ROOT
        val entries = store.list(path)

        // 돌려주는 path 도 원격 경로 공간이어야 한다. 상대가 그대로 화면에 쓰고
        // 다음 요청에 다시 보내기 때문이다
        reply(id, "list.ok") {
            put("path", RemotePath.normalize(path))
            put("entries", entries.toJson())
        }
    }

    private suspend fun onStat(id: Int, header: JSONObject) {
        val stat = store.stat(header.text("path"))
        reply(id, "stat.ok") {
            put("exists", stat.exists)
            put("is_dir", stat.isDir)
            put("size", stat.size)
        }
    }

    private suspend fun onTree(id: Int, header: JSONObject) {
        val tree = store.tree(header.text("path"))
        reply(id, "tree.ok") {
            put("files", JSONArray().apply {
                for (file in tree.files) {
                    put(JSONObject().put("path", file.path).put("size", file.size))
                }
            })
            put("dirs", JSONArray(tree.dirs))
        }
    }

    private suspend fun onHead(id: Int, header: JSONObject) {
        // 음수를 그대로 넘기면 파이썬에서 read(-1) 이 되어 파일을 통째로 읽는다.
        // 상한이 우회되므로 0 아래로는 내려가지 않게 잡는다
        val limit = header.optInt("limit", 0).coerceIn(0, Config.MAX_VIEW_BYTES)
        val head = store.readHead(header.text("path"), limit)
        send(
            JSONObject().put("t", "head.ok").put("id", id).put("size", head.size),
            head.bytes,
        )
    }

    private suspend fun onMkdir(id: Int, header: JSONObject) {
        store.mkdir(header.text("path"))
        reply(id, "mkdir.ok")
        onEvent(SessionEvent.Changed(RemotePath.parent(header.text("path"))))
    }

    private suspend fun onDelete(id: Int, header: JSONObject) {
        store.delete(header.text("path"))
        reply(id, "delete.ok")
        onEvent(SessionEvent.Changed(RemotePath.parent(header.text("path"))))
    }

    private suspend fun onRename(id: Int, header: JSONObject) {
        store.rename(header.text("path"), header.text("name") ?: "")
        reply(id, "rename.ok")
        onEvent(SessionEvent.Changed(RemotePath.parent(header.text("path"))))
    }

    private suspend fun onPull(id: Int, header: JSONObject) {
        store.openRead(header.text("path")).use { source ->
            val buffer = ByteArray(Config.CHUNK_SIZE)
            while (true) {
                val got = source.read(buffer)
                if (got <= 0) break
                // 응답이므로 q 를 붙이지 않는다
                send(JSONObject().put("t", "data").put("id", id), buffer.copyOf(got))
            }
        }
        reply(id, "data.end")
    }

    /** 밀어넣기를 수락하고, 같은 id 로 오는 data 프레임을 파일로 받아쓴다. */
    private suspend fun onPushBegin(id: Int, header: JSONObject, box: Channel<Incoming>) {
        val path = header.text("path")
        val writer = store.beginWrite(path)

        try {
            reply(id, "push.ok")
            while (true) {
                val frame = withTimeoutOrNull(Config.REQUEST_TIMEOUT_MS) {
                    try {
                        box.receive()
                    } catch (_: Exception) {
                        throw SessionError("전송 중 연결이 끊어졌습니다")
                    }
                } ?: throw SessionError("전송이 중간에 멈췄습니다")

                if (frame.header.optString("t") == "data.end") break
                frame.blob?.let { writer.write(it, it.size) }
            }
            writer.commit()
            reply(id, "push.done")
            onEvent(SessionEvent.Received(RemotePath.normalize(path)))
        } catch (exc: Throwable) {
            writer.abort()
            throw exc
        }
    }
}

// --- JSON 도우미 ---

/**
 * 문자열이 아니면 null. `optString` 은 없는 키에 `""` 를 주고 숫자도 문자열로
 * 바꿔주므로, 경로처럼 중요한 값에는 쓰지 않는다.
 */
private fun JSONObject.text(key: String): String? = opt(key) as? String

private fun List<Entry>.toJson(): JSONArray {
    val array = JSONArray()
    for (entry in this) {
        array.put(
            JSONObject()
                .put("name", entry.name)
                .put("is_dir", entry.isDir)
                .put("size", entry.size)
                .put("mtime", entry.mtimeSeconds),
        )
    }
    return array
}

private fun JSONObject.entries(): List<Entry> {
    val array = optJSONArray("entries") ?: return emptyList()
    val out = mutableListOf<Entry>()
    for (i in 0 until array.length()) {
        val item = array.optJSONObject(i) ?: continue
        val name = item.opt("name") as? String ?: continue
        out.add(
            Entry(
                name = name,
                isDir = item.optBoolean("is_dir"),
                size = item.optLong("size"),
                mtimeSeconds = item.optDouble("mtime", 0.0),
            ),
        )
    }
    return out
}

private fun JSONObject.treeFiles(): List<TreeFile> {
    val array = optJSONArray("files") ?: return emptyList()
    val out = mutableListOf<TreeFile>()
    for (i in 0 until array.length()) {
        val item = array.optJSONObject(i) ?: continue
        val path = item.opt("path") as? String ?: continue
        out.add(TreeFile(path, item.optLong("size")))
    }
    return out
}

private fun JSONObject.strings(key: String): List<String> {
    val array = optJSONArray(key) ?: return emptyList()
    return (0 until array.length()).mapNotNull { array.opt(it) as? String }
}
