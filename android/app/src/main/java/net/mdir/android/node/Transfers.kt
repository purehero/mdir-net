// 재귀 복사 — 폴더째 주고받는다. 진행률과 취소를 함께 다룬다
//
// 빈 폴더가 목록에 안 잡히는 것을 조심해야 한다. `tree` 가 files 와 dirs 를 따로
// 주는 이유가 그것이다. dirs 를 먼저 만들지 않으면 빈 폴더가 사라진다.

package net.mdir.android.node

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import net.mdir.android.net.Session
import net.mdir.android.store.Entry
import net.mdir.android.store.FileStore
import net.mdir.android.store.RemotePath
import net.mdir.android.store.Tree

/** 진행 중인 복사 하나. */
data class Progress(
    val label: String,
    val doneBytes: Long,
    val totalBytes: Long,
    val doneFiles: Int,
    val totalFiles: Int,
) {
    val fraction: Float
        get() = if (totalBytes <= 0) 0f else (doneBytes.toDouble() / totalBytes).toFloat()
}

class Transfers {

    private val _progress = MutableStateFlow<Progress?>(null)
    val progress: StateFlow<Progress?> = _progress.asStateFlow()

    private var job: Job? = null

    val busy: Boolean get() = job?.isActive == true

    fun cancel() {
        job?.cancel()
    }

    /**
     * 고른 항목들을 반대편으로 옮긴다.
     *
     * @param toRemote true 면 내 것을 상대에게(push), false 면 상대 것을 나에게(pull)
     */
    fun start(
        scope: CoroutineScope,
        session: Session,
        store: FileStore,
        items: List<Entry>,
        sourceDir: String,
        destDir: String,
        toRemote: Boolean,
        onDone: (String?) -> Unit,
    ) {
        if (busy) {
            onDone("이미 옮기는 중입니다")
            return
        }

        job = scope.launch(Dispatchers.IO) {
            try {
                run(session, store, items, sourceDir, destDir, toRemote)
                onDone(null)
            } catch (_: CancellationException) {
                onDone("취소했습니다")
            } catch (exc: Exception) {
                onDone(exc.message ?: "옮기지 못했습니다")
            } finally {
                _progress.value = null
            }
        }
    }

    private suspend fun run(
        session: Session,
        store: FileStore,
        items: List<Entry>,
        sourceDir: String,
        destDir: String,
        toRemote: Boolean,
    ) {
        // 먼저 전부 훑어 총량을 안다. 진행률을 보여주려면 필요하다
        val plan = plan(session, store, items, sourceDir, destDir, toRemote)

        _progress.value = Progress("준비 중", 0, plan.totalBytes, 0, plan.files.size)

        // 빈 폴더도 만들어야 한다. 파일만 옮기면 사라진다
        for (dir in plan.dirs) {
            makeDir(session, store, dir, toRemote)
        }

        var doneBytes = 0L
        for ((index, file) in plan.files.withIndex()) {
            _progress.value = Progress(
                label = file.name,
                doneBytes = doneBytes,
                totalBytes = plan.totalBytes,
                doneFiles = index,
                totalFiles = plan.files.size,
            )

            val moved = if (toRemote) {
                session.pushFile(file.source, file.dest) { sent ->
                    _progress.value = _progress.value?.copy(doneBytes = doneBytes + sent)
                }
            } else {
                session.pullFile(file.source, file.dest) { got ->
                    _progress.value = _progress.value?.copy(doneBytes = doneBytes + got)
                }
            }
            doneBytes += moved
        }

        _progress.value = Progress(
            "끝났습니다", doneBytes, plan.totalBytes, plan.files.size, plan.files.size,
        )
    }

    // --- 계획 ---

    private data class Move(val name: String, val source: String, val dest: String, val size: Long)

    private data class Plan(val files: List<Move>, val dirs: List<String>, val totalBytes: Long)

    private suspend fun plan(
        session: Session,
        store: FileStore,
        items: List<Entry>,
        sourceDir: String,
        destDir: String,
        toRemote: Boolean,
    ): Plan {
        val files = mutableListOf<Move>()
        val dirs = mutableListOf<String>()

        for (item in items) {
            val source = RemotePath.join(sourceDir, item.name)
            val dest = RemotePath.join(destDir, item.name)

            if (!item.isDir) {
                files.add(Move(item.name, source, dest, item.size))
                continue
            }

            dirs.add(dest)
            val tree = readTree(session, store, source, toRemote)
            for (sub in tree.dirs) dirs.add(RemotePath.join(dest, sub))
            for (sub in tree.files) {
                files.add(
                    Move(
                        name = sub.path.substringAfterLast('/'),
                        source = RemotePath.join(source, sub.path),
                        dest = RemotePath.join(dest, sub.path),
                        size = sub.size,
                    ),
                )
            }
        }

        return Plan(files, dirs, files.sumOf { it.size })
    }

    /** 훑는 쪽은 **보내는 쪽**이다. 내 것을 보내면 내 저장소를, 받으면 상대를 훑는다. */
    private suspend fun readTree(
        session: Session,
        store: FileStore,
        path: String,
        toRemote: Boolean,
    ): Tree = if (toRemote) store.tree(path) else session.remoteTree(path)

    /** 만드는 쪽은 **받는 쪽**이다. 이미 있으면 그냥 넘어간다. */
    private suspend fun makeDir(
        session: Session,
        store: FileStore,
        path: String,
        toRemote: Boolean,
    ) {
        try {
            if (toRemote) session.remoteMkdir(path) else store.mkdir(path)
        } catch (_: Exception) {
            // 이미 있는 폴더다. mkdir 은 그럴 때 실패하는 것이 규격이다
        }
    }
}
