// 공유 폴더 고르기 — 여기 넣은 폴더만 상대에게 열린다

package net.mdir.android.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import net.mdir.android.node.MDirNode

@Composable
fun SharesScreen() {
    val shares by remember { MDirNode.shares.shares }.collectAsState()
    val running by MDirNode.running.collectAsState()
    var removing by remember { mutableStateOf<String?>(null) }

    val pick = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocumentTree(),
    ) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        val added = MDirNode.shares.add(uri)
        MDirNode.notify(
            if (added == null) "권한을 받지 못했습니다" else "${added.name} 을(를) 공유합니다",
        )
    }

    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(
            "여기 넣은 폴더만 상대에게 보입니다. 상대는 그 안의 파일을 읽고 쓰고 지울 수 있습니다.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        Button(onClick = { pick.launch(null) }) { Text("폴더 추가") }

        if (running) {
            Text(
                "켜져 있는 동안 목록을 바꾸면 곧바로 반영됩니다.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        if (shares.isEmpty()) {
            Text("아직 없습니다.", style = MaterialTheme.typography.bodyMedium)
        }

        LazyColumn(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            items(shares, key = { it.key }) { share ->
                Card(Modifier.fillMaxWidth()) {
                    Row(
                        Modifier.padding(12.dp).fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(Modifier.weight(1f)) {
                            Text(share.name, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(
                                readable(share.key),
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                        }
                        TextButton(onClick = { removing = share.name }) { Text("빼기") }
                    }
                }
            }
        }
    }

    removing?.let { name ->
        AlertDialog(
            onDismissRequest = { removing = null },
            title = { Text("$name 을(를) 목록에서 뺄까요?") },
            // 실제 폴더는 남는다는 것을 분명히 해야 한다. 지우는 것으로 오해하면 큰일이다
            text = { Text("목록에서만 빠집니다. 폰 안의 실제 폴더와 파일은 그대로 있습니다.") },
            confirmButton = {
                TextButton(onClick = {
                    MDirNode.shares.remove(name)
                    removing = null
                }) { Text("빼기") }
            },
            dismissButton = { TextButton(onClick = { removing = null }) { Text("그만두기") } },
        )
    }
}

/** 트리 URI 에서 사람이 알아볼 부분만 뽑는다. */
private fun readable(key: String): String =
    key.substringAfterLast("/").replace("%3A", ":").replace("%2F", "/")
