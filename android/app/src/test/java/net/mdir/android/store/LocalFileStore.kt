// 진짜 파일시스템을 쓰는 FileStore — **테스트 전용**
//
// SAF 는 ContentResolver 가 있어야 하므로 JVM 테스트에서 쓸 수 없다. 세션의 핵심
// (q 규칙, 우편함, 동시 전송)을 검증하려면 뒤편에 파일이 있어야 하는데, 그것 때문에
// 기기 테스트로 밀려나면 결국 검사하지 않게 된다.
//
// 메모리 가짜가 아니라 진짜 파일을 쓴다. `.part` 후 rename, 부모 자동 생성, 재귀
// 삭제 같은 것을 실제로 겪어야 세션 쪽 검증이 의미가 있다. 파이썬 구현이 하는 일과
// 사실상 같아서, 이것과 맞으면 파이썬과도 맞을 가능성이 높다.
//
// 한계 — SAF 만의 사정(표시 이름 변형, 원자적 교체 없음, 공급자별 차이)은 이걸로
// 잡히지 않는다. 그쪽은 기기 실측으로 확인한다.

package net.mdir.android.store

import java.io.File
import java.io.InputStream
import java.io.OutputStream

class LocalFileStore(private val shares: ShareList) : FileStore {

    override fun rootEntries(): List<Entry> =
        shares.all().mapNotNull { share ->
            val dir = File(share.key)
            if (!dir.isDirectory) return@mapNotNull null
            Entry(share.name, isDir = true, size = 0, mtimeSeconds = dir.lastModified() / 1000.0)
        }.sortedForDisplay()

    override fun list(remotePath: String?): List<Entry> {
        if (isRoot(remotePath)) return rootEntries()

        val dir = resolve(remotePath)
        if (!dir.isDirectory) throw PathDenied("폴더가 아닙니다: ${dir.name}")

        return (dir.listFiles() ?: emptyArray()).map {
            Entry(
                name = it.name,
                isDir = it.isDirectory,
                size = if (it.isDirectory) 0 else it.length(),
                mtimeSeconds = it.lastModified() / 1000.0,
            )
        }.sortedForDisplay()
    }

    override fun stat(remotePath: String?): Stat {
        if (isRoot(remotePath)) return Stat(exists = true, isDir = true, size = 0)

        val file = runCatching { resolve(remotePath) }.getOrNull()
            ?: return Stat(exists = false, isDir = false, size = 0)
        if (!file.exists()) return Stat(exists = false, isDir = false, size = 0)

        return Stat(true, file.isDirectory, if (file.isDirectory) 0 else file.length())
    }

    override fun tree(remotePath: String?): Tree {
        val root = resolve(remotePath)
        val files = mutableListOf<TreeFile>()
        val dirs = mutableListOf<String>()

        fun walk(dir: File, prefix: String) {
            for (child in (dir.listFiles() ?: emptyArray()).sortedBy { it.name }) {
                val path = if (prefix.isEmpty()) child.name else "$prefix/${child.name}"
                if (child.isDirectory) {
                    dirs.add(path)
                    walk(child, path)
                } else {
                    files.add(TreeFile(path, child.length()))
                }
            }
        }
        walk(root, "")

        return Tree(files.sortedBy { it.path }, dirs.sorted())
    }

    override fun readHead(remotePath: String?, limit: Int): Head {
        val file = resolve(remotePath)
        if (!file.isFile) throw PathDenied("파일이 아닙니다: ${file.name}")
        if (limit <= 0) return Head(file.length(), ByteArray(0))

        file.inputStream().use { input ->
            val buffer = ByteArray(limit)
            var filled = 0
            while (filled < limit) {
                val got = input.read(buffer, filled, limit - filled)
                if (got < 0) break
                filled += got
            }
            return Head(file.length(), buffer.copyOf(filled))
        }
    }

    override fun mkdir(remotePath: String?) {
        val parts = requireInsideShare(remotePath, "공유 목록에는 폴더를 만들 수 없습니다")
        val target = resolveParts(parts)
        if (target.exists()) throw PathDenied("같은 이름이 이미 있습니다: ${target.name}")
        // 부모를 자동으로 만들지 않는다
        if (!target.parentFile!!.isDirectory) throw PathDenied("상위 폴더가 없습니다")
        if (!target.mkdir()) throw PathDenied("폴더를 만들지 못했습니다: ${target.name}")
    }

    override fun delete(remotePath: String?) {
        val parts = RemotePath.split(remotePath)
        if (parts.isEmpty()) throw PathDenied("공유 목록은 지울 수 없습니다")
        if (parts.size == 1) {
            throw PathDenied("공유 폴더 자체는 지울 수 없습니다. 목록에서 빼세요")
        }

        val target = resolve(remotePath)
        if (!target.exists()) throw PathDenied("없는 항목입니다: ${target.name}")
        if (!target.deleteRecursively()) throw PathDenied("지우지 못했습니다: ${target.name}")
    }

    override fun rename(remotePath: String?, newName: String) {
        if (!RemotePath.isValidName(newName)) throw PathDenied("이름이 올바르지 않습니다")

        val parts = RemotePath.split(remotePath)
        if (parts.isEmpty()) throw PathDenied("공유 목록은 이름을 바꿀 수 없습니다")
        if (parts.size == 1) throw PathDenied("공유 폴더 자체는 이름을 바꿀 수 없습니다")

        val source = resolve(remotePath)
        val target = File(source.parentFile, newName)
        // 덮어쓰지 않는다. POSIX rename 은 조용히 지우므로 먼저 막아야 한다
        if (target.exists()) throw PathDenied("같은 이름이 이미 있습니다: $newName")
        if (!source.renameTo(target)) throw PathDenied("이름을 바꾸지 못했습니다")
    }

    override fun openRead(remotePath: String?): InputStream {
        val file = resolve(remotePath)
        if (!file.isFile) throw PathDenied("파일이 아닙니다: ${file.name}")
        return file.inputStream()
    }

    override fun beginWrite(remotePath: String?): PartWriter {
        val parts = requireInsideShare(remotePath, "공유 목록에는 파일을 쓸 수 없습니다")
        val target = resolveParts(parts)
        // push 는 부모를 자동으로 만든다 (mkdir 과 다르다)
        target.parentFile!!.mkdirs()
        return LocalPartWriter(target)
    }

    // --- 경로 해석 ---

    private fun resolve(remotePath: String?): File {
        val parts = RemotePath.split(remotePath)
        if (parts.isEmpty()) throw PathDenied("가상 루트는 파일이 아닙니다")
        return resolveParts(parts)
    }

    /**
     * 조각들을 실제 파일로. 공유 밖을 가리키면 거부한다.
     *
     * `RemotePath.split` 이 `..` 을 이미 걷어내지만, 심링크로 밖을 가리킬 수는 있다.
     * 파이썬의 `resolve_within` 처럼 **펼친 최종 경로**가 공유 안쪽인지 본다.
     */
    private fun resolveParts(parts: List<String>): File {
        val share = shares.get(parts[0]) ?: throw PathDenied("공유 목록에 없습니다: ${parts[0]}")

        val root = File(share.key).canonicalFile
        var target = root
        for (piece in parts.drop(1)) target = File(target, piece)

        val real = target.canonicalFile
        if (real != root && !real.path.startsWith(root.path + File.separator)) {
            throw PathDenied("공유 범위를 벗어난 경로입니다")
        }
        return real
    }

    private fun requireInsideShare(remotePath: String?, whyRoot: String): List<String> {
        val parts = RemotePath.split(remotePath)
        if (parts.isEmpty()) throw PathDenied(whyRoot)
        if (parts.size == 1) throw PathDenied("공유 폴더 자체입니다: ${parts[0]}")
        return parts
    }
}

private class LocalPartWriter(private val target: File) : PartWriter {

    private val part = File(target.parentFile, target.name + ".part")
    private var output: OutputStream? = part.outputStream()
    private var done = false

    override fun write(bytes: ByteArray, length: Int) {
        (output ?: throw PathDenied("이미 끝난 전송입니다")).write(bytes, 0, length)
    }

    override fun commit() {
        if (done) return
        done = true

        output?.use { it.flush() }
        output = null

        // 파이썬의 os.replace 와 같다 — 있으면 덮어쓴다
        if (!part.renameTo(target)) {
            target.delete()
            if (!part.renameTo(target)) {
                part.delete()
                throw PathDenied("받은 파일을 옮기지 못했습니다: ${target.name}")
            }
        }
    }

    override fun abort() {
        if (done) return
        done = true

        try {
            output?.close()
        } catch (_: Exception) {
        }
        output = null
        part.delete()
    }
}
