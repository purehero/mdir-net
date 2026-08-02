// 화면 뼈대 — 어느 화면을 보여줄지 고르고, 어디서나 뜨는 것들(승인·진행률·알림)을 얹는다

package net.mdir.android.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import net.mdir.android.R
import net.mdir.android.node.MDirNode

enum class Screen { HOME, SHARES, BROWSER }

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MDirApp() {
    var screen by rememberSaveable { mutableStateOf(Screen.HOME) }

    val session by MDirNode.session.collectAsState()
    val approval by MDirNode.approval.collectAsState()
    val notice by MDirNode.notice.collectAsState()
    val progress by MDirNode.transfers.progress.collectAsState()

    val snackbar = remember { SnackbarHostState() }

    // 연결이 끊기면 상대 화면에 머물러 있을 이유가 없다
    LaunchedEffect(session) {
        if (session == null && screen == Screen.BROWSER) screen = Screen.HOME
    }

    LaunchedEffect(notice) {
        notice?.let {
            snackbar.showSnackbar(it.text)
            MDirNode.clearNotice()
        }
    }

    Scaffold(
        modifier = Modifier.fillMaxSize(),
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        when (screen) {
                            Screen.HOME -> stringResource(R.string.app_name)
                            Screen.SHARES -> "공유 폴더"
                            Screen.BROWSER -> session?.peerName ?: "파일"
                        },
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                },
                navigationIcon = {
                    if (screen != Screen.HOME) {
                        IconButton(onClick = { screen = Screen.HOME }) {
                            Icon(backArrow(), contentDescription = "뒤로")
                        }
                    }
                },
            )
        },
    ) { insets ->
        Column(Modifier.padding(insets).fillMaxSize()) {
            progress?.let { TransferBar(it) }

            when (screen) {
                Screen.HOME -> HomeScreen(
                    onOpenShares = { screen = Screen.SHARES },
                    onOpenBrowser = { screen = Screen.BROWSER },
                )
                Screen.SHARES -> SharesScreen()
                Screen.BROWSER -> BrowserScreen()
            }
        }
    }

    approval?.let { ApprovalDialog(it.name, it.host, it.platform) }
}

@Composable
private fun TransferBar(progress: net.mdir.android.node.Progress) {
    Card(
        modifier = Modifier.fillMaxWidth().padding(12.dp),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.secondaryContainer,
        ),
    ) {
        Column(Modifier.padding(12.dp)) {
            Text(
                "${progress.label}  (${progress.doneFiles}/${progress.totalFiles})",
                style = MaterialTheme.typography.bodyMedium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                "${humanSize(progress.doneBytes)} / ${humanSize(progress.totalBytes)}",
                style = MaterialTheme.typography.bodySmall,
            )
            LinearProgressIndicator(
                progress = { progress.fraction },
                modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
            )
            TextButton(onClick = { MDirNode.transfers.cancel() }) { Text("취소") }
        }
    }
}

@Composable
private fun ApprovalDialog(name: String, host: String, platform: String) {
    AlertDialog(
        onDismissRequest = { /* 반드시 골라야 한다. 밖을 눌러 넘길 수 없다 */ },
        title = { Text("$name 가 연결하려 합니다") },
        text = {
            Column {
                Text("$host  ($platform)")
                Text(
                    "수락하면 상대가 공유 폴더 안의 파일을 읽고, 쓰고, 지울 수 있습니다.",
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(top = 8.dp),
                )
            }
        },
        confirmButton = {
            TextButton(onClick = { MDirNode.decideApproval(true) }) { Text("수락") }
        },
        dismissButton = {
            TextButton(onClick = { MDirNode.decideApproval(false) }) { Text("거부") }
        },
    )
}

/** 머티리얼 아이콘 확장 라이브러리를 끌어오지 않으려고 직접 그린다. */
@Composable
private fun backArrow(): ImageVector = remember {
    androidx.compose.ui.graphics.vector.ImageVector.Builder(
        defaultWidth = 24.dp,
        defaultHeight = 24.dp,
        viewportWidth = 24f,
        viewportHeight = 24f,
    ).apply {
        addPath(
            pathData = androidx.compose.ui.graphics.vector.PathParser()
                .parsePathString("M20,11 H7.8 l5.6,-5.6 L12,4 l-8,8 8,8 1.4,-1.4 L7.8,13 H20 Z")
                .toNodes(),
            fill = androidx.compose.ui.graphics.SolidColor(
                androidx.compose.ui.graphics.Color.Black,
            ),
        )
    }.build()
}

fun humanSize(bytes: Long): String {
    if (bytes < 1024) return "$bytes B"
    var value = bytes.toDouble()
    for (unit in listOf("KB", "MB", "GB", "TB")) {
        value /= 1024.0
        if (value < 1024) return String.format("%.1f %s", value, unit)
    }
    return String.format("%.1f PB", value)
}
