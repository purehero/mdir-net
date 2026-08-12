// 공유 key 형식 — 트리 URI 와 `트리URI#하위/경로` 를 오가는 규칙
//
// 여기가 틀리면 공유 범위가 조용히 넓어진다. 그래서 잘못된 조각을 어떻게 다루는지를
// 왕복 검사보다 더 꼼꼼히 본다.

package com.purehero.netdir.store

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ShareKeyTest {

    private val tree = "content://com.android.externalstorage.documents/tree/primary%3ADownload%2Fmdir-net"

    @Test
    fun `하위 경로가 없으면 트리 URI 그대로다`() {
        assertEquals(tree, ShareKey.make(tree))
        assertEquals(tree, ShareKey.tree(tree))
        assertEquals(emptyList<String>(), ShareKey.sub(tree))
    }

    @Test
    fun `하위 경로를 붙이고 다시 떼어낸다`() {
        val key = ShareKey.make(tree, listOf("share"))

        assertEquals("$tree#share", key)
        assertEquals(tree, ShareKey.tree(key))
        assertEquals(listOf("share"), ShareKey.sub(key))
    }

    @Test
    fun `여러 칸 깊이도 왕복한다`() {
        val parts = listOf("share", "안쪽", "더 안쪽")
        val key = ShareKey.make(tree, parts)

        assertEquals(tree, ShareKey.tree(key))
        assertEquals(parts, ShareKey.sub(key))
    }

    // 아래 셋이 이 파일의 핵심이다. 잘못된 조각을 빈 목록으로 접으면 공유 폴더가
    // 트리 루트가 되어 **범위가 넓어진다.** 접근할 수 없는 편이 낫다
    @Test
    fun `빈 조각은 null 이다`() {
        assertNull(ShareKey.sub("$tree#"))
        assertNull(ShareKey.sub("$tree#share//안쪽"))
    }

    @Test
    fun `점과 두 점은 null 이다`() {
        assertNull(ShareKey.sub("$tree#."))
        assertNull(ShareKey.sub("$tree#.."))
        assertNull(ShareKey.sub("$tree#share/../../다른곳"))
    }

    @Test
    fun `조각이 잘못돼도 트리는 그대로 읽힌다`() {
        // 권한을 반납할 때 트리를 알아야 한다. 조각이 깨졌다고 그것까지 잃으면
        // 놓지 못한 권한이 남는다
        assertEquals(tree, ShareKey.tree("$tree#.."))
    }

    @Test
    fun `조각 없는 옛 key 는 그대로 동작한다`() {
        // 이 변경 전에 저장된 목록이 그대로 읽혀야 한다
        val old = "content://com.android.externalstorage.documents/tree/primary%3ADownload%2FTemp"

        assertEquals(old, ShareKey.tree(old))
        assertEquals(emptyList<String>(), ShareKey.sub(old))
    }
}
