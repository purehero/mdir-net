// 공유 폴더 고르기 — 여기 넣은 폴더만 상대에게 열린다

package net.mdir.android.ui

import android.net.Uri
import android.provider.DocumentsContract
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

/**
 * 피커를 열 위치. 내장 저장공간의 다운로드 폴더다.
 *
 * 시작 위치를 주지 않으면(`launch(null)`) 피커가 마지막 위치나 루트 목록에서 열린다.
 * 거기서 왼쪽의 **'다운로드'** 를 누르면 `externalstorage` 가 아니라 DownloadManager 가
 * 가진 목록이 열리는데, **파일 관리자로 만든 폴더가 거기엔 없어서 빈 화면처럼 보이고**
 * 트리로 고를 수도 없다. 그래서 진짜 파일시스템 쪽 다운로드 폴더를 못 박는다.
 * 다른 곳을 고르려면 피커 위쪽 경로에서 한 칸 올라가면 된다.
 *
 * 없는 폴더를 가리키면 피커가 기본 위치에서 열린다 — 예전과 같아지므로 손해는 없다.
 */
private val PICKER_START: Uri =
    DocumentsContract.buildDocumentUri("com.android.externalstorage.documents", "primary:Download")

@Composable
fun SharesScreen() {
    val shares by remember { MDirNode.shares.shares }.collectAsState()
    val running by MDirNode.running.collectAsState()
    var removing by remember { mutableStateOf<String?>(null) }

    val pick = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocumentTree(),
    ) { uri ->
        // 아무 말도 없이 돌아오면 사용자는 앱이 먹통이라고 느낀다. 그만뒀는지
        // 시스템이 결과를 흘렸는지도 구분할 수 없다 (접었다 펴면 실제로 흘린다)
        if (uri == null) {
            MDirNode.notify("폴더를 고르지 않았습니다")
            return@rememberLauncherForActivityResult
        }

        // 같은 폴더를 다시 고른 것은 실수지 새 공유가 아니다. add 가 기존 것을
        // 돌려주므로 목록이 그대로인데, 그걸 알려주지 않으면 선택이 안 먹은 줄 안다
        val already = shares.any { it.key == uri.toString() }
        val added = MDirNode.shares.add(uri)
        MDirNode.notify(
            when {
                added == null -> "권한을 받지 못했습니다"
                already -> "${added.name} 은(는) 이미 공유 중입니다"
                else -> "${added.name} 을(를) 공유합니다"
            },
        )
    }

    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(
            "여기 넣은 폴더만 상대에게 보입니다. 상대는 그 안의 파일을 읽고 쓰고 지울 수 있습니다.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        Button(onClick = { pick.launch(PICKER_START) }) { Text("폴더 추가") }

        // 안드로이드 11 부터 시스템이 아예 막는 곳이 있다. 눌러도 안 되는 이유를
        // 모르면 앱이 고장난 것으로 보인다
        Text(
            "다운로드 폴더와 저장소 최상위는 시스템이 공유 대상으로 고르지 못하게 막습니다. " +
                "그 안에 폴더를 하나 만들어 고르세요.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

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
