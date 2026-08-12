// 공유 폴더를 SAF(Storage Access Framework) 로 다룬다 — 폰 쪽 FileStore 구현
//
// 안드로이드 11 부터 /sdcard 직접 접근이 막혀 있고 MANAGE_EXTERNAL_STORAGE 는 스토어
// 심사 대상이다. 그래서 사용자가 ACTION_OPEN_DOCUMENT_TREE 로 고른 트리만 쓴다.
// 고른 트리 하나 = 공유 폴더 하나로 대응된다.
//
// 부수 효과로 보안이 오히려 나아진다. 파이썬은 심링크까지 따라간 최종 경로가 공유
// 폴더 안쪽인지 검사해야 하지만(fsops.resolve_within), 여기서는 트리 루트에서
// **표시 이름을 따라 한 칸씩 내려가는 것 말고 다른 방법이 없다.** 원시 경로를
// 밀어넣을 통로 자체가 없어서 경로 탈출이 구조적으로 불가능하다.

package net.mdir.android.store

import android.content.ContentResolver
import android.database.Cursor
import android.net.Uri
import android.provider.DocumentsContract
import java.io.InputStream
import java.io.OutputStream

/** SAF 문서 하나의 메타데이터. */
private data class Doc(
    val treeUri: Uri,
    val uri: Uri,
    val id: String,
    val name: String,
    val isDir: Boolean,
    val size: Long,
    val lastModifiedMs: Long,
)

class SafFileStore(
    private val resolver: ContentResolver,
    private val shares: ShareList,
) : FileStore {

    // --- 목록 ---

    override fun rootEntries(): List<Entry> =
        shares.all().mapNotNull { share ->
            // 없어진 트리(권한이 회수됐거나 SD 카드를 뺀 경우)는 내보내지 않는다.
            // 상대가 들어가도 실패할 항목을 보여줄 이유가 없다
            val root = runCatching { shareRoot(share) }.getOrNull() ?: return@mapNotNull null
            Entry(share.name, isDir = true, size = 0, mtimeSeconds = seconds(root.lastModifiedMs))
        }.sortedForDisplay()

    override fun list(remotePath: String?): List<Entry> {
        if (isRoot(remotePath)) return rootEntries()

        val dir = resolve(remotePath)
        if (!dir.isDir) throw PathDenied("폴더가 아닙니다: ${dir.name}")

        return children(dir).map {
            Entry(
                name = it.name,
                isDir = it.isDir,
                size = if (it.isDir) 0 else it.size,
                mtimeSeconds = seconds(it.lastModifiedMs),
            )
        }.sortedForDisplay()
    }

    override fun stat(remotePath: String?): Stat {
        if (isRoot(remotePath)) return Stat(exists = true, isDir = true, size = 0)

        val doc = runCatching { resolve(remotePath) }.getOrNull()
            ?: return Stat(exists = false, isDir = false, size = 0)

        return Stat(exists = true, isDir = doc.isDir, size = if (doc.isDir) 0 else doc.size)
    }

    override fun tree(remotePath: String?): Tree {
        val root = resolve(remotePath)
        val files = mutableListOf<TreeFile>()
        val dirs = mutableListOf<String>()

        // 재귀 대신 스택을 쓴다. 깊은 트리에서 스택이 넘치지 않게
        val pending = ArrayDeque<Pair<Doc, String>>()
        pending.add(root to "")

        while (pending.isNotEmpty()) {
            val (dir, prefix) = pending.removeFirst()
            // 읽을 수 없는 하위 폴더는 조용히 건너뛴다. 하나 때문에 전체가 실패하면 안 된다
            val kids = runCatching { children(dir) }.getOrDefault(emptyList())

            for (kid in kids) {
                val path = if (prefix.isEmpty()) kid.name else "$prefix/${kid.name}"
                if (kid.isDir) {
                    dirs.add(path)
                    pending.add(kid to path)
                } else {
                    files.add(TreeFile(path, kid.size))
                }
            }
        }

        return Tree(files.sortedBy { it.path }, dirs.sorted())
    }

    override fun readHead(remotePath: String?, limit: Int): Head {
        val doc = resolve(remotePath)
        if (doc.isDir) throw PathDenied("파일이 아닙니다: ${doc.name}")

        val bytes = if (limit <= 0) {
            ByteArray(0)
        } else {
            open(doc.uri).use { input ->
                val buffer = ByteArray(limit)
                var filled = 0
                while (filled < limit) {
                    val got = input.read(buffer, filled, limit - filled)
                    if (got < 0) break
                    filled += got
                }
                buffer.copyOf(filled)
            }
        }
        return Head(doc.size, bytes)
    }

    // --- 바꾸는 것 ---

    override fun mkdir(remotePath: String?) {
        val parts = requireInsideShare(remotePath, "공유 목록에는 폴더를 만들 수 없습니다")
        val name = parts.last()
        val parent = resolve(RemotePath.join(parts.dropLast(1)))

        if (child(parent, name) != null) throw PathDenied("같은 이름이 이미 있습니다: $name")

        create(parent, DocumentsContract.Document.MIME_TYPE_DIR, name)
            ?: throw PathDenied("폴더를 만들지 못했습니다: $name")
    }

    override fun delete(remotePath: String?) {
        val parts = RemotePath.split(remotePath)
        if (parts.isEmpty()) throw PathDenied("공유 목록은 지울 수 없습니다")
        if (parts.size == 1) {
            throw PathDenied("공유 폴더 자체는 지울 수 없습니다. 목록에서 빼세요")
        }

        val doc = resolve(remotePath)
        // SAF 의 deleteDocument 는 디렉터리를 재귀로 지운다
        if (!DocumentsContract.deleteDocument(resolver, doc.uri)) {
            throw PathDenied("지우지 못했습니다: ${doc.name}")
        }
    }

    override fun rename(remotePath: String?, newName: String) {
        if (!RemotePath.isValidName(newName)) throw PathDenied("이름이 올바르지 않습니다")

        val parts = RemotePath.split(remotePath)
        if (parts.isEmpty()) throw PathDenied("공유 목록은 이름을 바꿀 수 없습니다")
        if (parts.size == 1) throw PathDenied("공유 폴더 자체는 이름을 바꿀 수 없습니다")

        val doc = resolve(remotePath)
        val parent = resolve(RemotePath.join(parts.dropLast(1)))
        // 덮어쓰지 않는다. 파이썬 쪽도 같은 이름이 있으면 거부한다
        if (child(parent, newName) != null) throw PathDenied("같은 이름이 이미 있습니다: $newName")

        DocumentsContract.renameDocument(resolver, doc.uri, newName)
            ?: throw PathDenied("이름을 바꾸지 못했습니다: ${doc.name}")
    }

    override fun openRead(remotePath: String?): InputStream {
        val doc = resolve(remotePath)
        if (doc.isDir) throw PathDenied("파일이 아닙니다: ${doc.name}")
        return open(doc.uri)
    }

    override fun beginWrite(remotePath: String?): PartWriter {
        val parts = requireInsideShare(remotePath, "공유 목록에는 파일을 쓸 수 없습니다")
        val name = parts.last()
        // push 는 부모를 자동으로 만든다 (mkdir 과 다르다)
        val parent = ensureDirs(parts.dropLast(1))

        return SafPartWriter(resolver, parent, name)
    }

    // --- 경로 해석 ---

    /** 원격 경로를 문서로. 이 클래스에서 경로를 다루는 **유일한** 통로다. */
    private fun resolve(remotePath: String?): Doc {
        val parts = RemotePath.split(remotePath)
        if (parts.isEmpty()) throw PathDenied("가상 루트는 파일이 아닙니다")

        val share = shares.get(parts[0])
            ?: throw PathDenied("공유 목록에 없습니다: ${parts[0]}")

        var doc = shareRoot(share)
        for (piece in parts.drop(1)) {
            doc = child(doc, piece) ?: throw PathDenied("없는 항목입니다: $piece")
        }
        return doc
    }

    /** 공유 폴더 안쪽을 가리키는지 확인하고 조각을 돌려준다. */
    private fun requireInsideShare(remotePath: String?, whyRoot: String): List<String> {
        val parts = RemotePath.split(remotePath)
        if (parts.isEmpty()) throw PathDenied(whyRoot)
        if (parts.size == 1) throw PathDenied("공유 폴더 자체입니다: ${parts[0]}")
        return parts
    }

    /**
     * 공유 폴더의 문서. key 에 하위 경로가 붙어 있으면 트리 루트에서 내려간다.
     *
     * 내려가는 방법은 다른 경로 해석과 **똑같이** 표시 이름을 따라 한 칸씩이다.
     * 원시 경로를 밀어넣을 통로는 여전히 없으므로 경로 탈출은 그대로 불가능하다.
     */
    private fun shareRoot(share: ShareRef): Doc {
        val treeUri = Uri.parse(ShareKey.tree(share.key))
        val rootId = DocumentsContract.getTreeDocumentId(treeUri)
        val uri = DocumentsContract.buildDocumentUriUsingTree(treeUri, rootId)

        var doc = meta(treeUri, uri, rootId)
            ?: throw PathDenied("공유 폴더에 접근할 수 없습니다: ${share.name}")

        val sub = ShareKey.sub(share.key)
            ?: throw PathDenied("공유 위치가 잘못됐습니다: ${share.name}")

        for (piece in sub) {
            doc = child(doc, piece)?.takeIf { it.isDir }
                ?: throw PathDenied("공유 폴더에 접근할 수 없습니다: ${share.name}")
        }
        return doc
    }

    /** 없는 중간 폴더를 만들며 내려간다. push 의 부모 만들기에 쓴다. */
    private fun ensureDirs(parts: List<String>): Doc {
        val share = shares.get(parts[0]) ?: throw PathDenied("공유 목록에 없습니다: ${parts[0]}")

        var doc = shareRoot(share)
        for (piece in parts.drop(1)) {
            val existing = child(doc, piece)
            doc = when {
                existing == null ->
                    create(doc, DocumentsContract.Document.MIME_TYPE_DIR, piece)
                        ?: throw PathDenied("폴더를 만들지 못했습니다: $piece")
                existing.isDir -> existing
                else -> throw PathDenied("같은 이름의 파일이 있습니다: $piece")
            }
        }
        return doc
    }

    // --- SAF 저수준 ---

    /**
     * 자식 목록을 **쿼리 한 번**으로 가져온다.
     *
     * `DocumentFile.listFiles()` 는 자식마다 따로 쿼리를 날려 폴더가 조금만 커도
     * 눈에 띄게 느리다. 목록은 화면을 그릴 때마다 필요하므로 여기서는 쓰지 않는다.
     */
    private fun children(dir: Doc): List<Doc> {
        if (!dir.isDir) return emptyList()

        val childrenUri =
            DocumentsContract.buildChildDocumentsUriUsingTree(dir.treeUri, dir.id)

        return query(childrenUri) { cursor ->
            val out = mutableListOf<Doc>()
            while (cursor.moveToNext()) {
                // 이름이나 id 가 없는 줄은 건너뛴다. 목록 전체를 버리는 것보다 낫다
                fromCursor(dir.treeUri, cursor)?.let { out.add(it) }
            }
            out
        } ?: emptyList()
    }

    private fun child(dir: Doc, name: String): Doc? =
        children(dir).firstOrNull { it.name == name }

    private fun meta(treeUri: Uri, uri: Uri, fallbackId: String): Doc? =
        query(uri) { cursor ->
            if (cursor.moveToFirst()) fromCursor(treeUri, cursor, fallbackId) else null
        }

    private fun create(parent: Doc, mimeType: String, name: String): Doc? {
        val uri = DocumentsContract.createDocument(resolver, parent.uri, mimeType, name)
            ?: return null
        val id = DocumentsContract.getDocumentId(uri)
        return meta(parent.treeUri, uri, id)
    }

    private fun open(uri: Uri): InputStream =
        resolver.openInputStream(uri) ?: throw PathDenied("파일을 열 수 없습니다")

    private fun <T> query(uri: Uri, body: (Cursor) -> T): T? =
        try {
            resolver.query(uri, PROJECTION, null, null, null)?.use(body)
        } catch (_: Exception) {
            null
        }

    private fun fromCursor(treeUri: Uri, cursor: Cursor, fallbackId: String? = null): Doc? {
        val id = cursor.stringOrNull(DocumentsContract.Document.COLUMN_DOCUMENT_ID)
            ?: fallbackId
            ?: return null
        val name = cursor.stringOrNull(DocumentsContract.Document.COLUMN_DISPLAY_NAME)
            ?: return null
        val mime = cursor.stringOrNull(DocumentsContract.Document.COLUMN_MIME_TYPE)

        return Doc(
            treeUri = treeUri,
            uri = DocumentsContract.buildDocumentUriUsingTree(treeUri, id),
            id = id,
            name = name,
            isDir = mime == DocumentsContract.Document.MIME_TYPE_DIR,
            size = cursor.longOrZero(DocumentsContract.Document.COLUMN_SIZE),
            lastModifiedMs = cursor.longOrZero(DocumentsContract.Document.COLUMN_LAST_MODIFIED),
        )
    }

    private fun Cursor.stringOrNull(column: String): String? {
        val at = getColumnIndex(column)
        return if (at < 0 || isNull(at)) null else getString(at)
    }

    private fun Cursor.longOrZero(column: String): Long {
        val at = getColumnIndex(column)
        return if (at < 0 || isNull(at)) 0L else getLong(at)
    }

    private fun seconds(millis: Long) = millis / 1000.0

    private companion object {
        val PROJECTION = arrayOf(
            DocumentsContract.Document.COLUMN_DOCUMENT_ID,
            DocumentsContract.Document.COLUMN_DISPLAY_NAME,
            DocumentsContract.Document.COLUMN_MIME_TYPE,
            DocumentsContract.Document.COLUMN_SIZE,
            DocumentsContract.Document.COLUMN_LAST_MODIFIED,
        )
    }
}

/**
 * `<이름>.part` 로 받아쓰고 완료할 때 제 이름으로 바꾼다.
 *
 * SAF 공급자가 MIME 을 보고 확장자를 덧붙이거나 겹치는 이름에 `(1)` 을 붙이는 일이
 * 있다. 그래서 만든 뒤 **실제로 붙은 이름을 다시 읽어** 두고, 마지막에 rename 으로
 * 목표 이름을 확정한다.
 */
private class SafPartWriter(
    private val resolver: ContentResolver,
    parent: Doc,
    private val target: String,
) : PartWriter {

    private val parentUri = parent.uri
    private val treeUri = parent.treeUri
    private val partUri: Uri
    private var output: OutputStream?
    private var done = false

    init {
        partUri = DocumentsContract.createDocument(
            resolver,
            parentUri,
            "application/octet-stream",
            "$target.part",
        ) ?: throw PathDenied("받을 파일을 만들지 못했습니다: $target")

        output = resolver.openOutputStream(partUri, "w")
            ?: run {
                DocumentsContract.deleteDocument(resolver, partUri)
                throw PathDenied("받을 파일을 열지 못했습니다: $target")
            }
    }

    override fun write(bytes: ByteArray, length: Int) {
        (output ?: throw PathDenied("이미 끝난 전송입니다")).write(bytes, 0, length)
    }

    override fun commit() {
        if (done) return
        done = true

        output?.use { it.flush() }
        output = null

        // 밀어넣기는 덮어쓴다 (파이썬은 os.replace 로 원자적으로 한다). SAF 에는
        // 그런 원자적 교체가 없어 지우고 바꾸는 수밖에 없다. 그 사이에 앱이 죽으면
        // 목표 파일이 없어진 채로 .part 만 남는다 — 다시 받으면 된다
        existingTarget()?.let { DocumentsContract.deleteDocument(resolver, it) }

        DocumentsContract.renameDocument(resolver, partUri, target)
            ?: throw PathDenied("받은 파일의 이름을 바꾸지 못했습니다: $target")
    }

    override fun abort() {
        if (done) return
        done = true

        try {
            output?.close()
        } catch (_: Exception) {
        }
        output = null

        // 반쪽짜리가 남지 않게 지운다
        try {
            DocumentsContract.deleteDocument(resolver, partUri)
        } catch (_: Exception) {
        }
    }

    private fun existingTarget(): Uri? {
        val parentId = DocumentsContract.getDocumentId(parentUri)
        val childrenUri = DocumentsContract.buildChildDocumentsUriUsingTree(treeUri, parentId)
        val projection = arrayOf(
            DocumentsContract.Document.COLUMN_DOCUMENT_ID,
            DocumentsContract.Document.COLUMN_DISPLAY_NAME,
        )

        return try {
            resolver.query(childrenUri, projection, null, null, null)?.use { cursor ->
                while (cursor.moveToNext()) {
                    if (cursor.getString(1) == target) {
                        return@use DocumentsContract.buildDocumentUriUsingTree(
                            treeUri,
                            cursor.getString(0),
                        )
                    }
                }
                null
            }
        } catch (_: Exception) {
            null
        }
    }
}
