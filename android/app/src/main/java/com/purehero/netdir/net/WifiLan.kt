// LAN 을 쓰기 위한 안드로이드 쪽 준비 — MulticastLock 과 Wi-Fi 네트워크 바인딩
//
// Discovery 와 Session 이 안드로이드 클래스를 몰라도 되도록 여기에 몰아둔다.
// 두 가지를 해결한다.
//
//  1. Wi-Fi 스택은 자기 앞으로 오지 않은 패킷을 기본으로 걸러낸다. MulticastLock 을
//     잡지 않으면 브로드캐스트 알림이 앱까지 올라오지 않는다. 보내는 건 되는데
//     못 받는 증상이 난다.
//  2. Wi-Fi 와 셀룰러가 함께 켜져 있으면 자바 소켓이 셀룰러로 나간다. 그러면 같은
//     공유기 안의 PC 에 닿지 않는다. 소켓을 Wi-Fi 네트워크에 명시적으로 묶어야 한다.

package com.purehero.netdir.net

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.wifi.WifiManager
import java.net.DatagramSocket
import java.net.Socket
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

class WifiLan(context: Context) {

    private val app = context.applicationContext

    private val wifi = app.getSystemService(Context.WIFI_SERVICE) as? WifiManager
    private val connectivity =
        app.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager

    private var lock: WifiManager.MulticastLock? = null
    private var callback: ConnectivityManager.NetworkCallback? = null

    @Volatile
    private var network: Network? = null

    /** Wi-Fi 로 LAN 에 붙어 있는지. 아니면 상대를 찾을 수 없다고 화면에 알린다. */
    private val _online = MutableStateFlow(false)
    val online: StateFlow<Boolean> = _online.asStateFlow()

    /**
     * 소켓을 Wi-Fi 네트워크에 묶는다. Discovery 와 Session 에 넘길 훅이다.
     *
     * 묶지 못해도 예외를 올리지 않는다. 기본 경로로 나가 LAN 에 안 닿을 뿐인데,
     * 여기서 실패로 처리하면 Wi-Fi 만 켜진 평범한 상황까지 함께 죽는다.
     */
    fun bind(socket: DatagramSocket) {
        try {
            network?.bindSocket(socket)
        } catch (_: Exception) {
        }
    }

    fun bind(socket: Socket) {
        try {
            network?.bindSocket(socket)
        } catch (_: Exception) {
        }
    }

    /** 서비스가 살아있는 동안만 잡는다. 배터리를 먹는다. */
    fun acquire() {
        if (lock == null) {
            lock = wifi?.createMulticastLock("mdirnet")?.apply {
                setReferenceCounted(false)
                try {
                    acquire()
                } catch (_: Exception) {
                }
            }
        }
        watchNetwork()
    }

    fun release() {
        try {
            lock?.takeIf { it.isHeld }?.release()
        } catch (_: Exception) {
        }
        lock = null

        callback?.let {
            try {
                connectivity?.unregisterNetworkCallback(it)
            } catch (_: Exception) {
            }
        }
        callback = null
        network = null
        _online.value = false
    }

    /**
     * 지금 붙어있는 Wi-Fi 네트워크를 따라다닌다.
     *
     * `requestNetwork` 가 아니라 `registerNetworkCallback` 을 쓴다. 관찰만 하면
     * 되므로 `CHANGE_NETWORK_STATE` 권한이 필요없다.
     *
     * `NET_CAPABILITY_INTERNET` 은 **요구하지 않는다.** 인터넷이 안 되는 공유기라도
     * 같은 LAN 안에서 파일을 주고받는 데는 아무 문제가 없다.
     */
    private fun watchNetwork() {
        if (callback != null) return
        val manager = connectivity ?: return

        val request = NetworkRequest.Builder()
            .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
            .build()

        val watcher = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(available: Network) {
                network = available
                _online.value = true
            }

            override fun onLost(lost: Network) {
                if (network == lost) {
                    network = null
                    _online.value = false
                }
            }
        }

        try {
            manager.registerNetworkCallback(request, watcher)
            callback = watcher
        } catch (_: Exception) {
            // 관찰에 실패해도 기본 경로로는 돌아간다
        }
    }
}
