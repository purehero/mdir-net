// 첫 화면 — 켜고 끄기, 내 상태, 찾은 상대 목록

package com.purehero.netdir.ui

import android.Manifest
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.purehero.netdir.node.MDirNode
import com.purehero.netdir.service.MDirService

@Composable
fun HomeScreen(onOpenShares: () -> Unit, onOpenBrowser: () -> Unit) {
    val context = LocalContext.current

    val running by MDirNode.running.collectAsState()
    val online by MDirNode.online.collectAsState()
    val peers by MDirNode.peers.collectAsState()
    val session by MDirNode.session.collectAsState()
    val port by MDirNode.port.collectAsState()
    val fingerprint by MDirNode.fingerprint.collectAsState()
    val shares by remember { MDirNode.shares.shares }.collectAsState()

    var name by rememberSaveable { mutableStateOf(Build.MODEL ?: "안드로이드") }

    // 알림 권한이 없으면 앱이 앞에 없을 때 승인을 물어볼 방법이 없다
    val askNotifications = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { }
    LaunchedEffect(Unit) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            askNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {

        // 이 경고는 임시가 아니다. 평문 전송이라는 사실은 계속 보여야 한다
        Card(
            colors = CardDefaults.cardColors(
                containerColor = MaterialTheme.colorScheme.errorContainer,
            ),
        ) {
            Text(
                // 암호화가 붙었다고 이 자리를 지우면 안 된다. 막지 못하는 것이 남아 있고,
                // 그것을 말하지 않으면 사용자가 실제보다 안전하다고 믿는다
                "전송은 암호화됩니다. 다만 상대가 누구인지는 확인하지 않으니 " +
                    "신뢰하는 집 안 네트워크에서만 쓰세요.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onErrorContainer,
                modifier = Modifier.padding(12.dp),
            )
        }

        OutlinedTextField(
            value = name,
            onValueChange = {
                name = it
                if (running) MDirNode.rename(it)
            },
            label = { Text("이 기기 이름") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(
                onClick = {
                    if (running) MDirService.stop(context) else MDirService.start(context, name)
                },
                enabled = running || shares.isNotEmpty(),
            ) {
                Text(if (running) "끄기" else "켜기")
            }
            OutlinedButton(onClick = onOpenShares) { Text("공유 폴더 (${shares.size})") }
        }

        Text(
            when {
                shares.isEmpty() -> "공유할 폴더를 먼저 고르세요. 그 폴더만 상대에게 열립니다."
                !running -> "꺼져 있습니다. 켜야 상대가 나를 찾을 수 있습니다."
                !online -> "Wi-Fi 를 기다리는 중입니다. 같은 공유기에 붙어 있어야 합니다."
                else -> "기다리는 중입니다.  포트 $port"
            },
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        // 상대가 걸어올 때 그쪽 화면에 뜨는 `상대 지문` 과 대조할 값이다.
        // 켤 때마다 바뀐다 - 인증서를 저장하지 않기 때문이다
        fingerprint?.let {
            Text(
                "이 기기 지문  $it",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        session?.let { live ->
            HorizontalDivider()
            Card(Modifier.fillMaxWidth().clickable { onOpenBrowser() }) {
                Column(Modifier.padding(12.dp)) {
                    Text("${live.peerName} 와 연결됨", style = MaterialTheme.typography.titleMedium)
                    Text(live.peerHost, style = MaterialTheme.typography.bodySmall)
                    // 내가 건 경우에만 상대 인증서를 받는다. 이 값이 상대 화면의
                    // `이 기기 지문` 과 같아야 중간에 아무도 없는 것이다
                    live.peerFingerprint?.let {
                        Text(
                            "상대 지문  $it",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    Row(
                        Modifier.fillMaxWidth().padding(top = 8.dp),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Button(onClick = onOpenBrowser) { Text("파일 보기") }
                        OutlinedButton(onClick = { MDirNode.disconnect() }) { Text("끊기") }
                    }
                }
            }
        }

        HorizontalDivider()
        Text("찾은 기기", style = MaterialTheme.typography.titleMedium)

        if (peers.isEmpty()) {
            Text(
                if (running) {
                    "아직 아무도 없습니다. 상대 쪽 MDirNet 이 켜져 있는지, 같은 공유기인지 확인하세요."
                } else {
                    "켜면 같은 공유기 안의 기기를 찾습니다."
                },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        LazyColumn(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            items(peers, key = { it.nodeId }) { peer ->
                Card(
                    Modifier.fillMaxWidth().clickable(enabled = session == null) {
                        MDirNode.connectTo(peer)
                    },
                ) {
                    Row(
                        Modifier.padding(12.dp).fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(Modifier.weight(1f)) {
                            Text(peer.name, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(
                                "${peer.host}:${peer.port}  ${peer.platform}",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        Spacer(Modifier.width(8.dp))
                        if (session == null) Text("연결", color = MaterialTheme.colorScheme.primary)
                    }
                }
            }
        }
    }
}
