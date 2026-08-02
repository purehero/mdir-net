// 앱 진입점 — 지금은 뼈대다. 화면 구성은 UI 단계에서 채운다

package net.mdir.android

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import net.mdir.android.ui.MDirNetTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            MDirNetTheme {
                Scaffold(modifier = Modifier.fillMaxSize()) { insets ->
                    Placeholder(Modifier.padding(insets))
                }
            }
        }
    }
}

@Composable
private fun Placeholder(modifier: Modifier = Modifier) {
    Column(
        modifier = modifier.fillMaxSize().padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("MDirNet", style = MaterialTheme.typography.headlineMedium)
        Text(
            "같은 공유기 안의 PC 와 파일을 주고받습니다.",
            style = MaterialTheme.typography.bodyMedium,
        )
        // 이 경고는 임시가 아니다. 평문 전송이라는 사실은 화면에 계속 남아 있어야 한다
        Text(
            "전송은 암호화되지 않습니다. 신뢰하는 집 안 네트워크에서만 쓰세요.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.error,
        )
    }
}
