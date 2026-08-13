// 전송 암호화 - TCP 위에 TLS 1.3 을 씌운다 (PROTOCOL.md 0.1절)
//
// 파이썬 mdirnet/secure.py 의 거울이다. 규칙이 갈리면 두 구현이 아예 못 붙는다.
//
// 키를 남기지 않는다. 켤 때 만들고 끌 때 지운다. 저장하지 않으므로 보호할 것이
// 없다 - APK 를 뜯어도 얻을 것이 없다.
//
// 인증서는 목적이 아니라 TLS 가 공개키를 알아먹는 형식일 뿐이다. CA 도 신뢰
// 저장소도 없고, 상대 인증서를 검증하지 않는다.
//
//   막는다     같은 망에서의 패킷 캡처, 공유기의 트래픽 기록
//   못 막는다  능동적 중간자 (상대가 누구인지 확인할 수단이 없다)

package com.purehero.netdir.net

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.math.BigInteger
import java.net.Socket
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.SecureRandom
import java.security.cert.X509Certificate
import java.util.Calendar
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket
import javax.net.ssl.X509TrustManager
import javax.security.auth.x500.X500Principal

object Secure {

    private const val KEYSTORE = "AndroidKeyStore"

    /** 이 접두어가 붙은 항목은 전부 우리 것이다. 남은 것을 지울 때 쓴다. */
    private const val ALIAS_PREFIX = "netdir-tls-"

    private const val PROTOCOL = "TLSv1.3"

    /** 프로세스 한 판짜리 신원. `close()` 하면 키스토어에서 지운다. */
    class Identity(val alias: String, val certificate: X509Certificate) {
        val certificateDer: ByteArray get() = certificate.encoded

        fun close() {
            runCatching { keyStore().deleteEntry(alias) }
        }
    }

    /**
     * 켤 때마다 새로 만든다.
     *
     * 안드로이드 키스토어를 쓰는 이유는 자기서명 인증서를 **같이 내주기** 때문이다.
     * 공개 JDK API 로는 X.509 인증서를 만들 수 없어서, 이것 없이는 BouncyCastle
     * 같은 의존성을 더하거나 ASN.1 을 손으로 짜야 한다.
     *
     * 덤으로 개인키가 하드웨어 뒤에 남아 앱 프로세스도 꺼낼 수 없다. 메모리에
     * 들고 있는 것보다 낫다.
     */
    fun newIdentity(): Identity {
        dropLeftovers()

        val alias = ALIAS_PREFIX + BigInteger(64, SecureRandom()).toString(16)

        val notBefore = Calendar.getInstance().apply { add(Calendar.DAY_OF_YEAR, -1) }
        val notAfter = Calendar.getInstance().apply { add(Calendar.YEAR, 10) }

        val generator = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, KEYSTORE)
        generator.initialize(
            KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_SIGN or KeyProperties.PURPOSE_VERIFY)
                .setDigests(KeyProperties.DIGEST_SHA256)
                .setCertificateSubject(X500Principal("CN=mdirnet-node"))
                .setCertificateSerialNumber(BigInteger(64, SecureRandom()))
                .setCertificateNotBefore(notBefore.time)
                .setCertificateNotAfter(notAfter.time)
                .build(),
        )
        generator.generateKeyPair()

        val certificate = keyStore().getCertificate(alias) as X509Certificate
        return Identity(alias, certificate)
    }

    /**
     * 지난 실행이 남긴 항목을 지운다.
     *
     * 정상 종료면 `close()` 가 지우지만 강제 종료되면 남는다. 쌓이면 키스토어가
     * 지저분해지므로 켤 때 한 번 훑는다.
     */
    fun dropLeftovers() {
        runCatching {
            val store = keyStore()
            store.aliases().toList()
                .filter { it.startsWith(ALIAS_PREFIX) }
                .forEach { runCatching { store.deleteEntry(it) } }
        }
    }

    /** 받는 쪽. 인증서를 내밀고 상대에게는 요구하지 않는다. */
    fun wrapServer(socket: Socket, identity: Identity): SSLSocket =
        wrap(context(identity), socket, clientMode = false)

    /** 거는 쪽. 상대 인증서를 검증하지 않는다 - 검증할 체인이 없다. */
    fun wrapClient(socket: Socket): SSLSocket =
        wrap(context(null), socket, clientMode = true)

    /** 상대 인증서 DER. 못 받았으면 null. */
    fun peerCertificate(socket: SSLSocket): ByteArray? =
        runCatching { (socket.session.peerCertificates.firstOrNull() as? X509Certificate)?.encoded }
            .getOrNull()

    private fun wrap(context: SSLContext, socket: Socket, clientMode: Boolean): SSLSocket {
        val tls = context.socketFactory.createSocket(
            socket, socket.inetAddress.hostAddress, socket.port, true,
        ) as SSLSocket

        tls.useClientMode = clientMode
        // 1.3 만 켠다. 아래로 떨어지는 것을 허용하면 파이썬 쪽과 규칙이 갈린다
        tls.enabledProtocols = arrayOf(PROTOCOL)

        // 명시적으로 부른다. 부르지 않으면 첫 읽기/쓰기까지 실패가 미뤄져서,
        // 프레임을 읽다 터진 것처럼 보인다
        tls.startHandshake()
        return tls
    }

    private fun context(identity: Identity?): SSLContext {
        val managers = identity?.let {
            KeyManagerFactory.getInstance("PKIX").apply { init(keyStore(), null) }.keyManagers
        }

        return SSLContext.getInstance(PROTOCOL).apply {
            init(managers, arrayOf(AcceptAny), SecureRandom())
        }
    }

    private fun keyStore(): KeyStore = KeyStore.getInstance(KEYSTORE).apply { load(null) }

    /**
     * 아무 인증서나 받는다.
     *
     * 자기서명이라 검증할 체인이 없다. 여기서 무엇을 검사할 수 있는 것처럼 꾸미면
     * 읽는 사람이 오해한다. 중간자를 알아채는 것은 2.5절 지문을 사람이 대조하는
     * 몫이다.
     */
    private object AcceptAny : X509TrustManager {
        override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) = Unit
        override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) = Unit
        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
    }
}
