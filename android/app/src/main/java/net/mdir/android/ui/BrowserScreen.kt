// 파일 화면 — MDir 의 양쪽 패널을 폰에서는 탭 둘로 바꿨다
//
// 복사 방향은 지금 보고 있는 탭이 정한다. `내 폰` 에서 누르면 상대의 지금 폴더로
// 올려보내고, `상대` 에서 누르면 내 지금 폴더로 내려받는다. 양쪽 패널의 현재 폴더를
// 늘 기억하고 있어야 하는 이유다.

package net.mdir.android.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import net.mdir.android.net.Session
import net.mdir.android.node.MDirNode
import net.mdir.android.store.Entry
import net.mdir.android.store.RemotePath

@Composable
fun BrowserScreen() {
    val session by MDirNode.session.collectAsState()
    val live = session ?: return

    val scope = rememberCoroutineScope()
    val state = remember(live) { BrowserState(live, scope) }

    LaunchedEffect(live) { state.refreshBoth() }

    Column(Modifier.fillMaxSize()) {
        TabRow(selectedTabIndex = if (state.onRemote) 1 else 0) {
            Tab(
                selected = !state.onRemote,
                onClick = { state.onRemote = false },
                text = { Text("내 폰") },
            )
            Tab(
                selected = state.onRemote,
                onClick = { state.onRemote = true },
                text = { Text(live.peerName, maxLines = 1, overflow = TextOverflow.Ellipsis) },
            )
        }

        val pane = state.current

        Row(
            Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            // 왼쪽을 자른다. 뒤를 남겨야 지금 어느 폴더인지 보인다 (데스크톱과 같은 규칙)
            Text(
                tailOf(RemotePath.display(pane.path), 40),
                style = MaterialTheme.typography.bodySmall,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
            if (!RemotePath.isRoot(pane.path)) {
                TextButton(onClick = { state.goUp() }) { Text("위로") }
            }
        }

        HorizontalDivider()

        Box(Modifier.weight(1f)) {
            if (pane.loading) {
                CircularProgressIndicator(Modifier.align(Alignment.Center))
            } else if (pane.entries.isEmpty()) {
                Text(
                    pane.error ?: "비어 있습니다",
                    modifier = Modifier.align(Alignment.Center).padding(24.dp),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            } else {
                LazyColumn(Modifier.fillMaxSize()) {
                    items(pane.entries, key = { it.name }) { entry ->
                        EntryRow(
                            entry = entry,
                            checked = entry.name in pane.selection,
                            onToggle = { state.toggle(entry) },
                            onOpen = { state.open(entry) },
                        )
                    }
                }
            }
        }

        HorizontalDivider()
        Actions(state)
    }

    state.Dialogs()
}

@Composable
private fun EntryRow(entry: Entry, checked: Boolean, onToggle: () -> Unit, onOpen: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clickable { onOpen() }.padding(horizontal = 8.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Checkbox(checked = checked, onCheckedChange = { onToggle() })
        Column(Modifier.weight(1f)) {
            Text(
                if (entry.isDir) "[ ${entry.name} ]" else entry.name,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                style = MaterialTheme.typography.bodyMedium,
            )
            if (!entry.isDir) {
                Text(
                    humanSize(entry.size),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun Actions(state: BrowserState) {
    val pane = state.current
    val some = pane.selection.isNotEmpty()

    Row(
        Modifier.fillMaxWidth().padding(4.dp),
        horizontalArrangement = Arrangement.SpaceEvenly,
    ) {
        TextButton(onClick = { state.copySelected() }, enabled = some) {
            Text(if (state.onRemote) "받기" else "보내기")
        }
        TextButton(onClick = { state.asking = Asking.DELETE }, enabled = some) { Text("삭제") }
        TextButton(
            onClick = { state.asking = Asking.RENAME },
            enabled = pane.selection.size == 1,
        ) { Text("이름") }
        TextButton(onClick = { state.asking = Asking.MKDIR }) { Text("새 폴더") }
        TextButton(onClick = { state.refresh() }) { Text("새로고침") }
    }
}

/** 뒤에서부터 남긴다. 경로는 앞쪽보다 뒤쪽이 궁금하다. */
private fun tailOf(text: String, limit: Int): String =
    if (text.length <= limit) text else "~" + text.takeLast(limit - 1)

// --- 상태 ---

enum class Asking { NONE, DELETE, RENAME, MKDIR }

class Pane(val root: String) {
    var path by mutableStateOf(RemotePath.ROOT)
    var entries by mutableStateOf<List<Entry>>(emptyList())
    var selection by mutableStateOf<Set<String>>(emptySet())
    var loading by mutableStateOf(false)
    var error by mutableStateOf<String?>(null)
}

class BrowserState(private val session: Session, private val scope: CoroutineScope) {

    var onRemote by mutableStateOf(true)
    var asking by mutableStateOf(Asking.NONE)

    private val local = Pane("내 폰")
    private val remote = Pane("상대")

    val current: Pane get() = if (onRemote) remote else local
    private val other: Pane get() = if (onRemote) local else remote

    fun refreshBoth() {
        load(local, false)
        load(remote, true)
    }

    fun refresh() = load(current, onRemote)

    private fun load(pane: Pane, fromRemote: Boolean) {
        scope.launch {
            pane.loading = true
            pane.error = null
            try {
                val entries = if (fromRemote) {
                    session.remoteList(pane.path).second
                } else {
                    withContext(Dispatchers.IO) { MDirNode.store().list(pane.path) }
                }
                pane.entries = entries
                // 목록이 바뀌면 없어진 항목의 선택은 버린다
                pane.selection = pane.selection intersect entries.map { it.name }.toSet()
            } catch (exc: Exception) {
                pane.entries = emptyList()
                pane.error = exc.message ?: "읽지 못했습니다"
            } finally {
                pane.loading = false
            }
        }
    }

    fun open(entry: Entry) {
        if (!entry.isDir) {
            toggle(entry)
            return
        }
        val pane = current
        pane.path = RemotePath.join(pane.path, entry.name)
        pane.selection = emptySet()
        load(pane, onRemote)
    }

    fun goUp() {
        val pane = current
        pane.path = RemotePath.parent(pane.path)
        pane.selection = emptySet()
        load(pane, onRemote)
    }

    fun toggle(entry: Entry) {
        val pane = current
        pane.selection = if (entry.name in pane.selection) {
            pane.selection - entry.name
        } else {
            pane.selection + entry.name
        }
    }

    fun copySelected() {
        val pane = current
        val items = pane.entries.filter { it.name in pane.selection }
        if (items.isEmpty()) return

        // 가상 루트에는 아무것도 쓸 수 없다. 공유 폴더 목록이지 폴더가 아니다
        if (RemotePath.isRoot(other.path)) {
            MDirNode.notify("받는 쪽에서 공유 폴더 안으로 먼저 들어가세요")
            return
        }

        MDirNode.transfers.start(
            scope = scope,
            session = session,
            store = MDirNode.store(),
            items = items,
            sourceDir = pane.path,
            destDir = other.path,
            toRemote = !onRemote,
        ) { error ->
            MDirNode.notify(error ?: "${items.size}개를 옮겼습니다")
            pane.selection = emptySet()
            refreshBoth()
        }
    }

    private fun act(what: String, body: suspend () -> Unit) {
        scope.launch {
            try {
                body()
                refresh()
            } catch (exc: Exception) {
                MDirNode.notify(exc.message ?: "$what 하지 못했습니다")
            }
        }
    }

    @Composable
    fun Dialogs() {
        val pane = current
        when (asking) {
            Asking.NONE -> Unit

            Asking.DELETE -> AlertDialog(
                onDismissRequest = { asking = Asking.NONE },
                title = { Text("${pane.selection.size}개를 지울까요?") },
                text = { Text("되돌릴 수 없습니다. 폴더는 안에 든 것까지 함께 지워집니다.") },
                confirmButton = {
                    TextButton(onClick = {
                        val names = pane.selection.toList()
                        asking = Asking.NONE
                        act("삭제") {
                            for (name in names) {
                                val path = RemotePath.join(pane.path, name)
                                if (onRemote) {
                                    session.remoteDelete(path)
                                } else {
                                    withContext(Dispatchers.IO) { MDirNode.store().delete(path) }
                                }
                            }
                            pane.selection = emptySet()
                        }
                    }) { Text("지우기") }
                },
                dismissButton = {
                    TextButton(onClick = { asking = Asking.NONE }) { Text("그만두기") }
                },
            )

            Asking.RENAME -> TextDialog(
                title = "새 이름",
                initial = pane.selection.first(),
                confirm = "바꾸기",
                onDismiss = { asking = Asking.NONE },
            ) { newName ->
                val old = RemotePath.join(pane.path, pane.selection.first())
                asking = Asking.NONE
                act("이름 바꾸기") {
                    if (onRemote) {
                        session.remoteRename(old, newName)
                    } else {
                        withContext(Dispatchers.IO) { MDirNode.store().rename(old, newName) }
                    }
                    pane.selection = emptySet()
                }
            }

            Asking.MKDIR -> TextDialog(
                title = "새 폴더 이름",
                initial = "",
                confirm = "만들기",
                onDismiss = { asking = Asking.NONE },
            ) { newName ->
                val path = RemotePath.join(pane.path, newName)
                asking = Asking.NONE
                act("폴더 만들기") {
                    if (onRemote) {
                        session.remoteMkdir(path)
                    } else {
                        withContext(Dispatchers.IO) { MDirNode.store().mkdir(path) }
                    }
                }
            }
        }
    }
}

@Composable
private fun TextDialog(
    title: String,
    initial: String,
    confirm: String,
    onDismiss: () -> Unit,
    onConfirm: (String) -> Unit,
) {
    var text by remember { mutableStateOf(initial) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = {
            OutlinedTextField(
                value = text,
                onValueChange = { text = it },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
        },
        confirmButton = {
            TextButton(
                onClick = { onConfirm(text.trim()) },
                enabled = RemotePath.isValidName(text.trim()),
            ) { Text(confirm) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("그만두기") } },
    )
}
