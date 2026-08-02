// 화면이 꺼져도 상대가 내 파일을 가져갈 수 있게 노드를 살려두는 포그라운드 서비스
//
// "양방향 전체" 는 PC 가 폰의 파일을 가져갈 수 있다는 뜻이고, 그러려면 앱이 앞에
// 없어도 TCP 리스너와 UDP announcer 가 돌아야 한다. 안드로이드에서 그러려면
// 포그라운드 서비스뿐이다.
//
// API 35 부터 dataSync 는 24시간당 6시간 상한이 있다. 그래서 '항상 켜짐' 이 아니라
// 사용자가 켜고 끄는 모델로 간다. 알림에 '중지' 를 둔 이유다.

package net.mdir.android.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch
import net.mdir.android.MainActivity
import net.mdir.android.R
import net.mdir.android.net.ApprovalRequest
import net.mdir.android.node.MDirNode

class MDirService : Service() {

    private var scope: CoroutineScope? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createChannels()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                stopEverything()
                return START_NOT_STICKY
            }
            ACTION_APPROVE -> {
                MDirNode.decideApproval(true)
                return START_STICKY
            }
            ACTION_REJECT -> {
                MDirNode.decideApproval(false)
                return START_STICKY
            }
        }

        val name = intent?.getStringExtra(EXTRA_NAME) ?: defaultName()
        startForegroundSafely(buildStatus("켜는 중"))

        MDirNode.onApprovalNeeded = ::askApproval
        MDirNode.onApprovalSettled = ::clearApproval
        MDirNode.start(this, name)

        if (!MDirNode.running.value) {
            // 포트를 못 잡았다. 알림만 남기고 조용히 물러난다
            stopEverything()
            return START_NOT_STICKY
        }

        watchStatus()
        return START_STICKY
    }

    override fun onDestroy() {
        MDirNode.onApprovalNeeded = null
        MDirNode.onApprovalSettled = null
        scope?.cancel()
        scope = null
        super.onDestroy()
    }

    /** 상태가 바뀔 때마다 알림 줄을 고쳐 쓴다. */
    private fun watchStatus() {
        if (scope != null) return
        val work = CoroutineScope(Dispatchers.Main + SupervisorJob())
        scope = work

        work.launch {
            combine(MDirNode.session, MDirNode.port, MDirNode.online) { session, port, online ->
                when {
                    session != null -> "${session.peerName} 와 연결됨"
                    !online -> "Wi-Fi 를 기다리는 중"
                    else -> "기다리는 중  (포트 $port)"
                }
            }.collect { line ->
                notifications().notify(STATUS_ID, buildStatus(line))
            }
        }
    }

    private fun stopEverything() {
        MDirNode.stop()
        clearApproval()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            stopForeground(STOP_FOREGROUND_REMOVE)
        } else {
            @Suppress("DEPRECATION")
            stopForeground(true)
        }
        stopSelf()
    }

    // --- 알림 ---

    private fun startForegroundSafely(notification: Notification) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(STATUS_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(STATUS_ID, notification)
        }
    }

    private fun buildStatus(line: String): Notification =
        NotificationCompat.Builder(this, CHANNEL_STATUS)
            .setSmallIcon(R.drawable.ic_status)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(line)
            .setOngoing(true)
            .setSilent(true)
            .setContentIntent(openApp())
            .addAction(0, "중지", serviceIntent(ACTION_STOP))
            .build()

    /**
     * 앱이 앞에 없을 때 승인을 묻는다.
     *
     * 전체 화면 인텐트는 안드로이드 14 부터 제한되므로 쓰지 않는다. 우선순위 높은
     * 알림에 수락/거부를 달아 잠금화면 위로 뜨게 한다.
     */
    private fun askApproval(request: ApprovalRequest) {
        val notification = NotificationCompat.Builder(this, CHANNEL_APPROVAL)
            .setSmallIcon(R.drawable.ic_status)
            .setContentTitle("${request.name} 가 연결하려 합니다")
            .setContentText("${request.host} (${request.platform})")
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_CALL)
            .setAutoCancel(true)
            .setContentIntent(openApp())
            .addAction(0, "수락", serviceIntent(ACTION_APPROVE))
            .addAction(0, "거부", serviceIntent(ACTION_REJECT))
            .build()

        runCatching { notifications().notify(APPROVAL_ID, notification) }
    }

    private fun clearApproval() {
        runCatching { notifications().cancel(APPROVAL_ID) }
    }

    private fun notifications() = NotificationManagerCompat.from(this)

    private fun createChannels() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = getSystemService(NotificationManager::class.java) ?: return

        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_STATUS, "실행 상태", NotificationManager.IMPORTANCE_LOW)
                .apply { description = "MDirNet 이 켜져 있는 동안 남는 알림" },
        )
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_APPROVAL, "연결 요청", NotificationManager.IMPORTANCE_HIGH)
                .apply { description = "다른 기기가 연결하려 할 때 묻는 알림" },
        )
    }

    private fun openApp(): PendingIntent = PendingIntent.getActivity(
        this,
        0,
        Intent(this, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP),
        PendingIntent.FLAG_IMMUTABLE,
    )

    private fun serviceIntent(action: String): PendingIntent = PendingIntent.getService(
        this,
        action.hashCode(),
        Intent(this, MDirService::class.java).setAction(action),
        PendingIntent.FLAG_IMMUTABLE,
    )

    private fun defaultName() = Build.MODEL ?: "안드로이드"

    companion object {
        private const val CHANNEL_STATUS = "status"
        private const val CHANNEL_APPROVAL = "approval"
        private const val STATUS_ID = 1
        private const val APPROVAL_ID = 2

        const val ACTION_STOP = "net.mdir.android.STOP"
        const val ACTION_APPROVE = "net.mdir.android.APPROVE"
        const val ACTION_REJECT = "net.mdir.android.REJECT"
        const val EXTRA_NAME = "name"

        fun start(context: Context, name: String) {
            val intent = Intent(context, MDirService::class.java).putExtra(EXTRA_NAME, name)
            context.startForegroundService(intent)
        }

        fun stop(context: Context) {
            context.startService(
                Intent(context, MDirService::class.java).setAction(ACTION_STOP),
            )
        }
    }
}
