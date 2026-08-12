// 공유 목록 — 이름 겹침 처리와 깨진 저장본 다루기

package com.purehero.netdir.store

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ShareListTest {

    @Test
    fun `이름이 겹치면 숫자를 붙인다`() {
        val list = ShareList()
        assertEquals("사진", list.add("key1", "사진").name)
        assertEquals("사진 (2)", list.add("key2", "사진").name)
        assertEquals("사진 (3)", list.add("key3", "사진").name)
    }

    @Test
    fun `같은 폴더를 두 번 고르면 그대로 돌려준다`() {
        // 실수지 새 공유가 아니다
        val list = ShareList()
        val first = list.add("같은키", "사진")
        val again = list.add("같은키", "사진")

        assertEquals(first, again)
        assertEquals(1, list.all().size)
    }

    @Test
    fun `이름에 든 구분자를 밀어낸다`() {
        // 그대로 두면 경로 조각과 헷갈린다
        val list = ShareList()
        assertEquals("a_b_c", list.add("k", """a/b\c""").name)
    }

    @Test
    fun `이름이 비면 대체 이름을 쓴다`() {
        val list = ShareList()
        assertEquals("공유", list.add("k", "").name)
        assertEquals("공유 (2)", list.add("k2", "   ").name)
    }

    @Test
    fun `목록에서 뺀다`() {
        val list = ShareList()
        list.add("k", "사진")

        assertTrue(list.remove("사진"))
        assertTrue(list.isEmpty())
        assertNull(list.get("사진"))
    }

    @Test
    fun `저장하고 다시 읽는다`() {
        val list = ShareList()
        list.add("content://tree/primary%3ADownload", "다운로드")
        list.add("content://tree/primary%3APictures", "사진 모음")

        val back = ShareList.fromJson(list.toJson())

        assertEquals(list.all(), back.all())
        assertNotNull(back.get("사진 모음"))
    }

    @Test
    fun `깨진 저장본은 빈 목록으로 시작한다`() {
        // 목록 하나 때문에 앱이 못 뜨면 안 된다
        for (text in listOf(null, "", "  ", "깨졌다", "{}", """{"a":1}""")) {
            assertTrue("'$text' 에서 살아남지 못했다", ShareList.fromJson(text).isEmpty())
        }
    }

    @Test
    fun `이상한 항목은 버리고 나머지는 읽는다`() {
        val text = """
            [
              {"name":"좋음","key":"k1"},
              {"name":"","key":"k2"},
              {"name":"키없음"},
              {"key":"k3"},
              {"name":"경로/조각","key":"k4"},
              {"name":"좋음","key":"k5"},
              "문자열",
              {"name":"좋음2","key":"k6"}
            ]
        """.trimIndent()

        val list = ShareList.fromJson(text)

        assertEquals(listOf("좋음", "좋음2"), list.names())
    }
}
