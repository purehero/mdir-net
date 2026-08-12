// 상대에게 열어줄 공유 폴더 목록 — mdirnet/shares.py 의 ShareSet 에 해당한다
//
// 저장소가 무엇인지는 모른다. `key` 는 SAF 트리 URI 든 파일 경로든 구현체가
// 알아보는 불투명한 문자열이다. 덕분에 이 파일은 JVM 테스트에서 그대로 돈다.

package com.purehero.netdir.store

import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject

/** 공유 폴더 하나. `name` 은 상대에게 보이는 이름, `key` 는 내 저장소가 아는 값. */
data class ShareRef(val name: String, val key: String)

class ShareList(initial: List<ShareRef> = emptyList()) {

    private val shares = initial.toMutableList()

    @Synchronized
    fun all(): List<ShareRef> = shares.toList()

    @Synchronized
    fun names(): List<String> = shares.map { it.name }

    @Synchronized
    fun get(name: String): ShareRef? = shares.firstOrNull { it.name == name }

    @Synchronized
    fun isEmpty(): Boolean = shares.isEmpty()

    /**
     * 목록에 넣고 돌려준다. 같은 key 가 이미 있으면 그것을 돌려준다.
     *
     * 같은 폴더를 두 번 고르는 것은 실수지 새 공유가 아니다.
     */
    @Synchronized
    fun add(key: String, preferredName: String): ShareRef {
        shares.firstOrNull { it.key == key }?.let { return it }

        val share = ShareRef(uniqueName(preferredName), key)
        shares.add(share)
        return share
    }

    @Synchronized
    fun remove(name: String): Boolean = shares.removeAll { it.name == name }

    /**
     * 폴더 이름을 그대로 쓰고, 겹치면 뒤에 숫자를 붙인다.
     *
     * `/` 나 `\` 가 들어가면 경로 조각과 헷갈리므로 밀어낸다. 이름이 비는 경우도
     * 있어(트리 루트 등) 그때는 대체 이름을 만든다.
     */
    private fun uniqueName(preferred: String): String {
        val base = preferred.replace('/', '_').replace('\\', '_').trim().ifEmpty { "공유" }

        val taken = shares.map { it.name }.toSet()
        if (base !in taken) return base

        for (suffix in 2..999) {
            val candidate = "$base ($suffix)"
            if (candidate !in taken) return candidate
        }
        // 같은 이름이 998개다. 여기까지 오면 key 를 붙여서라도 겹치지 않게 한다
        return "$base (${key(base)})"
    }

    private fun key(base: String) = base.hashCode().toUInt().toString(16)

    // --- 저장 / 불러오기 ---

    @Synchronized
    fun toJson(): String {
        val array = JSONArray()
        for (share in shares) {
            array.put(JSONObject().put("name", share.name).put("key", share.key))
        }
        return array.toString()
    }

    companion object {
        /** 깨졌거나 이상한 항목은 버리고 읽는다. 목록 하나 때문에 앱이 못 뜨면 안 된다. */
        fun fromJson(text: String?): ShareList {
            if (text.isNullOrBlank()) return ShareList()

            val array = try {
                JSONArray(text)
            } catch (_: JSONException) {
                return ShareList()
            }

            val out = mutableListOf<ShareRef>()
            for (i in 0 until array.length()) {
                val item = array.optJSONObject(i) ?: continue
                val name = item.opt("name") as? String ?: continue
                val key = item.opt("key") as? String ?: continue
                if (name.isEmpty() || key.isEmpty()) continue
                // 경로 조각과 헷갈리는 이름은 버린다
                if (name.contains('/') || name.contains('\\')) continue
                if (out.any { it.name == name }) continue
                out.add(ShareRef(name, key))
            }
            return ShareList(out)
        }
    }
}
