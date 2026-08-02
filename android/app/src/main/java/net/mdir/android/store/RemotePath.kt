// 상대와 주고받는 경로를 다룬다 — mdirnet/shares.py 의 거울 (PROTOCOL.md 6절)
//
// 선 위의 경로는 호스트 절대 경로가 아니라 **공유 상대 POSIX 문자열**이다.
//     "."                     가상 루트 (공유 목록)
//     "Downloads"             공유 폴더 하나
//     "프로젝트/src/main.kt"   그 안의 파일
// 호스트 경로가 새어나가지 않고, 안드로이드가 Windows 경로를 다룰 일도 없다.

package net.mdir.android.store

object RemotePath {

    /** 가상 루트를 가리키는 값. */
    const val ROOT = "."

    /**
     * 경로를 조각으로 나누고 `..` 을 제대로 처리한다.
     *
     * `..` 을 그냥 버리면 `공유/a/../b` 가 `공유/a/b` 로 해석되어 의도와 달라진다.
     * 앞 조각을 지우는 방식으로 처리하되 **빈 목록보다 아래로는 내려가지 않는다.**
     * 그래서 아무리 `..` 을 쌓아도 최대 가상 루트까지만 올라간다.
     */
    fun split(remotePath: String?): List<String> {
        val text = (remotePath ?: "").replace('\\', '/')

        val parts = mutableListOf<String>()

        // POSIX 는 **정확히 두 개**로 시작하는 슬래시를 구현에 맡긴다. 파이썬
        // `PurePosixPath` 는 그것을 `//` 조각으로 남긴다 (세 개 이상은 접는다).
        // 기괴하지만 참조 구현이 그렇게 하므로 그대로 맞춘다 — 경로 해석은 보안
        // 경계라 두 구현이 한 글자도 갈리면 안 된다.
        //
        // 결과적으로는 늘 거부로 끝난다. 공유 이름에는 `/` 를 넣을 수 없어
        // (ShareList.uniqueName) `//` 라는 공유가 존재할 수 없기 때문이다.
        if (text.startsWith("//") && !text.startsWith("///")) parts.add("//")

        for (piece in text.split('/')) {
            when (piece) {
                "", "." -> continue
                ".." -> if (parts.isNotEmpty()) parts.removeAt(parts.size - 1)
                else -> parts.add(piece)
            }
        }
        return parts
    }

    /** 조각들로 경로를 만든다. 구분자는 항상 `/` 다. */
    fun join(parts: List<String>): String =
        if (parts.isEmpty()) ROOT else parts.joinToString("/")

    fun join(vararg parts: String?): String =
        join(parts.filterNotNull().filter { it.isNotEmpty() && it != ROOT }
            .flatMap { split(it) })

    /** 한 단계 위. 가상 루트에서는 더 올라가지 않는다. */
    fun parent(remotePath: String?): String = split(remotePath).let {
        if (it.isEmpty()) ROOT else join(it.subList(0, it.size - 1))
    }

    /** 표준 형태로 다듬는다. `..` 과 빈 조각을 없앤 결과. */
    fun normalize(remotePath: String?): String = join(split(remotePath))

    /** 화면에 보여줄 형태. 가상 루트는 `/` 다. */
    fun display(remotePath: String?): String = split(remotePath).let {
        if (it.isEmpty()) "/" else "/" + it.joinToString("/")
    }

    fun isRoot(remotePath: String?): Boolean = split(remotePath).isEmpty()

    /** 이름 하나로 쓸 수 있는지. `rename` 이 받는 값을 검사한다. */
    fun isValidName(name: String?): Boolean =
        !name.isNullOrEmpty() &&
            !name.contains('/') &&
            !name.contains('\\') &&
            name != "." &&
            name != ".."
}
