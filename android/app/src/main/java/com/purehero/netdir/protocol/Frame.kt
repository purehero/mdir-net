// TCP 위에서 JSON 헤더와 raw 바이너리를 함께 실어보내는 프레이밍 계층 (PROTOCOL.md 3절)

package com.purehero.netdir.protocol

import java.io.EOFException
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.nio.ByteBuffer
import java.nio.charset.CharacterCodingException
import java.nio.charset.CodingErrorAction
import org.json.JSONException
import org.json.JSONObject

/** 프레임이 규격에 맞지 않을 때. 발생하면 연결을 끊는다. */
class ProtocolError(message: String) : IOException(message)

/** 상대가 연결을 정상/비정상 종료했을 때. */
class PeerClosed(message: String) : IOException(message)

/**
 * 프레임 = 4바이트 빅엔디안 헤더길이 + UTF-8 JSON 헤더 + (blob 바이트)
 *
 * blob 이 있으면 헤더에 그 길이를 `blob` 키로 박아둔다. 리더가 헤더를 읽은 직후
 * 정확히 그만큼 더 읽으면 되므로 별도 구분자가 필요없다.
 */
object Frame {

    /** 헤더와 선택적 raw 바이트를 하나의 프레임으로 직렬화한다. */
    fun encode(header: JSONObject, blob: ByteArray? = null): ByteArray {
        var out = header
        if (blob != null) {
            if (blob.size > Config.MAX_BLOB_BYTES) {
                throw ProtocolError("blob 이 상한을 넘었습니다: ${blob.size}")
            }
            // 호출한 쪽의 객체를 건드리지 않는다. 파이썬의 dict(header, blob=..) 와 같다
            out = copyWith(header, "blob", blob.size)
        }

        val raw = out.toString().toByteArray(Charsets.UTF_8)
        if (raw.size > Config.MAX_HEADER_BYTES) {
            throw ProtocolError("헤더가 상한을 넘었습니다: ${raw.size}")
        }

        val frame = ByteArray(4 + raw.size + (blob?.size ?: 0))
        writeLength(frame, raw.size)
        raw.copyInto(frame, 4)
        blob?.copyInto(frame, 4 + raw.size)
        return frame
    }

    /** 프레임 하나를 스트림에 완전히 써넣는다. */
    fun send(sink: OutputStream, header: JSONObject, blob: ByteArray? = null) {
        sink.write(encode(header, blob))
        sink.flush()
    }

    private fun copyWith(header: JSONObject, key: String, value: Int): JSONObject {
        val copy = JSONObject()
        for (name in header.keys()) {
            copy.put(name, header.get(name))
        }
        copy.put(key, value)
        return copy
    }

    private fun writeLength(target: ByteArray, size: Int) {
        target[0] = (size ushr 24).toByte()
        target[1] = (size ushr 16).toByte()
        target[2] = (size ushr 8).toByte()
        target[3] = size.toByte()
    }
}

/** 읽어낸 프레임 하나. */
data class Incoming(val header: JSONObject, val blob: ByteArray?) {
    // JSONObject 와 ByteArray 는 값 비교가 되지 않는다. data class 의 자동 구현을
    // 그대로 두면 의미 없는 동일성 비교가 생기므로 참조 비교로 못박는다
    override fun equals(other: Any?) = this === other
    override fun hashCode() = System.identityHashCode(this)
}

/**
 * 스트림에서 프레임을 하나씩 꺼내 읽는다.
 *
 * 한 연결에 리더는 반드시 하나여야 한다. 헤더와 blob 을 연속으로 읽는 구조라
 * 둘 이상이 같은 스트림을 읽으면 섞여서 프로토콜이 깨진다.
 */
class FrameReader(source: InputStream) {

    // 소켓 스트림을 그대로 읽으면 1 바이트씩 시스템 콜이 나간다. 파이썬 쪽이
    // 버퍼를 직접 들고 있는 것과 같은 역할이다
    private val input = source.buffered(64 * 1024)

    /** 다음 프레임을 읽는다. blob 이 없으면 null. */
    fun read(): Incoming {
        val size = readLength()
        if (size > Config.MAX_HEADER_BYTES) {
            throw ProtocolError("헤더 길이가 상한을 넘었습니다: $size")
        }

        val header = try {
            JSONObject(decodeUtf8(readExactly(size.toInt())))
        } catch (exc: JSONException) {
            throw ProtocolError("헤더를 해석할 수 없습니다: ${exc.message}")
        }

        val blobLen = blobLength(header)
        header.remove("blob")

        return Incoming(header, if (blobLen > 0) readExactly(blobLen) else null)
    }

    /**
     * 엄격한 UTF-8 디코딩.
     *
     * `String(bytes, UTF_8)` 은 잘못된 바이트를 예외 없이 U+FFFD 로 바꿔치운다.
     * 참조 구현(파이썬)은 `UnicodeDecodeError` 로 연결을 끊으므로 동작이 갈린다.
     * 깨진 상대를 조용히 받아주면 파일 이름이 뭉개진 채로 흘러들어간다.
     */
    private fun decodeUtf8(raw: ByteArray): String {
        val decoder = Charsets.UTF_8.newDecoder()
            .onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT)
        return try {
            decoder.decode(ByteBuffer.wrap(raw)).toString()
        } catch (exc: CharacterCodingException) {
            throw ProtocolError("헤더가 UTF-8 이 아닙니다: ${exc.message}")
        }
    }

    /**
     * 헤더의 blob 길이. 없으면 0.
     *
     * 정수가 아니면 거부한다. `has` 로만 확인하고 `optInt` 를 쓰면 문자열 "abc" 가
     * 0 으로 조용히 넘어가 blob 을 통째로 못 읽고 스트림이 어긋난다.
     */
    private fun blobLength(header: JSONObject): Int {
        if (!header.has("blob")) return 0

        val value = header.get("blob")
        if (value !is Int && value !is Long) {
            throw ProtocolError("blob 길이가 올바르지 않습니다")
        }

        val length = (value as Number).toLong()
        if (length < 0) throw ProtocolError("blob 길이가 올바르지 않습니다")
        if (length > Config.MAX_BLOB_BYTES) {
            throw ProtocolError("blob 길이가 상한을 넘었습니다: $length")
        }
        return length.toInt()
    }

    /**
     * 4바이트 빅엔디안 길이. 부호 없는 값으로 읽는다.
     *
     * Int 로 그대로 담으면 0x80000000 이상이 음수가 되어 상한 검사를 통과해 버린다.
     */
    private fun readLength(): Long {
        val head = readExactly(4)
        return ((head[0].toLong() and 0xFF) shl 24) or
            ((head[1].toLong() and 0xFF) shl 16) or
            ((head[2].toLong() and 0xFF) shl 8) or
            (head[3].toLong() and 0xFF)
    }

    private fun readExactly(n: Int): ByteArray {
        val out = ByteArray(n)
        var filled = 0
        while (filled < n) {
            val got = try {
                input.read(out, filled, n - filled)
            } catch (exc: EOFException) {
                throw PeerClosed("상대가 연결을 닫았습니다")
            }
            if (got < 0) throw PeerClosed("상대가 연결을 닫았습니다")
            filled += got
        }
        return out
    }
}
