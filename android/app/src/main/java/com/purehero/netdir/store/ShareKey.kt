// 공유 하나를 가리키는 key 의 형식 — 트리 URI, 또는 `트리URI#하위/경로`
//
// SAF 트리 권한은 피커로만 받는다. 그래서 `Download/mdir-net` 에 권한을 받아도
// 그 안의 `share` 는 독립된 트리 URI 가 되지 못한다. 하위 경로를 key 에 붙여
// **한 번 받은 트리 안에서** 공유 위치를 가리킨다.
//
// 안드로이드에 기대지 않는다 (Uri 를 쓰지 않는다). 그래야 JVM 테스트로 검증된다.

package com.purehero.netdir.store

object ShareKey {

    /** 트리 URI 문자열에는 조각(fragment)이 없으므로 구분자로 안전하다. */
    private const val SEPARATOR = '#'

    fun make(treeUri: String, sub: List<String> = emptyList()): String =
        if (sub.isEmpty()) treeUri else treeUri + SEPARATOR + sub.joinToString("/")

    /** 권한을 받은 트리. 조각이 없으면 key 자체다. */
    fun tree(key: String): String = key.substringBefore(SEPARATOR)

    /**
     * 트리 루트에서 공유 폴더까지 내려갈 이름들. 조각이 없으면 빈 목록.
     *
     * **잘못된 조각은 빈 목록이 아니라 null 이다.** 빈 목록으로 접으면 공유 범위가
     * 트리 루트로 **넓어진다** — 조용히 넓어지느니 접근할 수 없는 편이 낫다.
     */
    fun sub(key: String): List<String>? {
        if (SEPARATOR !in key) return emptyList()

        val parts = key.substringAfter(SEPARATOR).split('/')
        if (parts.any { it.isEmpty() || it == "." || it == ".." }) return null
        return parts
    }
}
