// 앱 진입점 — 화면을 띄우고, 화면이 앞에 있는지 노드에 알려준다

package net.mdir.android

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import net.mdir.android.node.MDirNode
import net.mdir.android.ui.MDirApp
import net.mdir.android.ui.MDirNetTheme

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        // 서비스가 꺼져 있어도 공유 목록은 고칠 수 있어야 한다
        MDirNode.ensureShares(this)

        setContent {
            MDirNetTheme {
                MDirApp()
            }
        }
    }

    /**
     * 화면이 앞에 있으면 승인을 다이얼로그로 묻는다.
     *
     * 앞에 있는데 알림까지 띄우면 성가시고, 뒤에 있는데 다이얼로그만 띄우면
     * 아무도 못 본 채 30초가 지나간다.
     */
    override fun onStart() {
        super.onStart()
        MDirNode.uiVisible = true
    }

    override fun onStop() {
        MDirNode.uiVisible = false
        super.onStop()
    }
}
