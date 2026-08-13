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
import java.security.Principal
import java.security.PrivateKey
import java.security.SecureRandom
import java.security.cert.X509Certificate
import java.util.Calendar
import javax.net.ssl.KeyManager
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLEngine
import javax.net.ssl.SSLSocket
import javax.net.ssl.X509ExtendedKeyManager
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
                // **DIGEST_NONE 이 있어야 한다.** Conscrypt 는 자기가 해시한 뒤
                // NONEwithECDSA 로 서명을 맡긴다. SHA-256 만 열어두면 키스토어가
                // INCOMPATIBLE_DIGEST 로 거부하고, 양쪽에는 "상대가 끊었다" 로만 보인다
                .setDigests(
                    KeyProperties.DIGEST_NONE,
                    KeyProperties.DIGEST_SHA256,
                    KeyProperties.DIGEST_SHA384,
                    KeyProperties.DIGEST_SHA512,
                )
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
        val managers = identity?.let { arrayOf<KeyManager>(SingleKey(it)) }

        return SSLContext.getInstance(PROTOCOL).apply {
            init(managers, arrayOf(AcceptAny), SecureRandom())
        }
    }

    private fun keyStore(): KeyStore = KeyStore.getInstance(KEYSTORE).apply { load(null) }

    /**
     * 우리 것 하나만 내미는 키 매니저.
     *
     * `KeyManagerFactory` 를 쓰지 않는 이유가 있다. 안드로이드 키스토어의 개인키는
     * **꺼낼 수 없다.** 표준 팩토리는 키를 꺼내 쓰려 해서 결국 아무 인증서도 내밀지
     * 못하고, 상대에게는 "connection closed" 로만 보인다. 여기서는 키스토어가 준
     * 불투명한 `PrivateKey` 를 그대로 넘긴다 - 서명은 키스토어 안에서 일어난다.
     */
    private class SingleKey(private val identity: Identity) : X509ExtendedKeyManager() {
        private val aliases = arrayOf(identity.alias)

        override fun chooseServerAlias(keyType: String?, issuers: Array<out Principal>?, socket: Socket?) =
            identity.alias

        override fun chooseClientAlias(keyType: Array<out String>?, issuers: Array<out Principal>?, socket: Socket?) =
            identity.alias

        /**
         * **엔진 경로용.** Conscrypt 의 소켓은 안쪽에서 `SSLEngine` 을 쓰므로 위의
         * `chooseServerAlias` 가 아니라 이쪽을 부른다. 기본 구현이 null 을 돌려주는
         * 바람에 서버가 인증서를 못 내밀었고, 양쪽에는 "상대가 끊었다" 로만 보였다.
         */
        override fun chooseEngineServerAlias(keyType: String?, issuers: Array<out Principal>?, engine: SSLEngine?) =
            identity.alias

        override fun chooseEngineClientAlias(keyType: Array<out String>?, issuers: Array<out Principal>?, engine: SSLEngine?) =
            identity.alias

        override fun getServerAliases(keyType: String?, issuers: Array<out Principal>?) = aliases

        override fun getClientAliases(keyType: String?, issuers: Array<out Principal>?) = aliases

        override fun getCertificateChain(alias: String?) = arrayOf(identity.certificate)

        override fun getPrivateKey(alias: String?): PrivateKey =
            keyStore().getKey(identity.alias, null) as PrivateKey
    }

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
