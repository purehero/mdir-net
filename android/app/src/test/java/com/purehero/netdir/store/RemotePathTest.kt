// 원격 경로 해석 — 파이썬 shares.py 의 _split 과 한 글자도 어긋나면 안 된다

package com.purehero.netdir.store

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RemotePathTest {

    @Test
    fun `평범한 경로를 조각으로 나눈다`() {
        assertEquals(listOf("Downloads"), RemotePath.split("Downloads"))
        assertEquals(listOf("프로젝트", "src", "main.kt"), RemotePath.split("프로젝트/src/main.kt"))
    }

    @Test
    fun `가상 루트를 나타내는 것들`() {
        for (text in listOf(".", "", "/", "./", "./.", "///", "////")) {
            assertTrue("'$text' 는 가상 루트여야 한다", RemotePath.isRoot(text))
            assertEquals(emptyList<String>(), RemotePath.split(text))
        }
        assertTrue(RemotePath.isRoot(null))
    }

    /**
     * 슬래시가 **정확히 두 개**로 시작하면 `//` 가 조각으로 남는다.
     *
     * POSIX 가 구현에 맡긴 자리라 파이썬 `PurePosixPath` 가 그렇게 한다. 기괴하지만
     * 참조 구현과 갈리면 안 되는 곳이라 그대로 맞췄다. 차분 검증
     * (`RemotePathGoldenTest`)이 이걸 잡아냈다.
     *
     * 실제로는 늘 거부로 끝난다. 공유 이름에 `/` 를 넣을 수 없어 `//` 라는 공유가
     * 존재할 수 없기 때문이다.
     */
    @Test
    fun `슬래시 두 개로 시작하면 조각이 남는다`() {
        assertEquals(listOf("//"), RemotePath.split("//"))
        assertEquals(listOf("//", "leading"), RemotePath.split("//leading"))
        assertFalse(RemotePath.isRoot("//"))

        // 세 개 이상은 접힌다
        assertEquals(listOf("leading"), RemotePath.split("///leading"))

        // `..` 은 이것도 지운다
        assertEquals(emptyList<String>(), RemotePath.split("//.."))
    }

    @Test
    fun `백슬래시도 구분자로 본다`() {
        // Windows 상대가 보낼 수 있다
        assertEquals(listOf("a", "b", "c"), RemotePath.split("""a\b\c"""))
        assertEquals(listOf("a", "b", "c"), RemotePath.split("""a\b/c"""))
    }

    /**
     * `..` 은 앞 조각을 지운다. 그냥 버리면 `공유/a/../b` 가 `공유/a/b` 로 해석되어
     * 사용자 의도와 달라진다.
     */
    @Test
    fun `점점은 앞 조각을 지운다`() {
        assertEquals(listOf("공유", "b"), RemotePath.split("공유/a/../b"))
        assertEquals(listOf("공유"), RemotePath.split("공유/a/.."))
    }

    /** 아무리 쌓아도 가상 루트보다 위로는 못 간다. 여기가 뚫리면 공유 밖이 열린다. */
    @Test
    fun `점점을 쌓아도 가상 루트에서 멈춘다`() {
        for (text in listOf("..", "../..", "../../../../..", "a/../../..", """..\..\..""")) {
            assertEquals("'$text' 가 루트를 넘었다", emptyList<String>(), RemotePath.split(text))
        }
        assertEquals(listOf("b"), RemotePath.split("../../b"))
    }

    @Test
    fun `정규화는 다듬은 결과를 돌려준다`() {
        assertEquals("공유/b", RemotePath.normalize("공유//a/../b/"))
        assertEquals(".", RemotePath.normalize("../.."))
        assertEquals(".", RemotePath.normalize(""))
    }

    @Test
    fun `한 단계 위로 간다`() {
        assertEquals("공유/a", RemotePath.parent("공유/a/b.txt"))
        assertEquals("공유", RemotePath.parent("공유/a"))
        assertEquals(".", RemotePath.parent("공유"))
        assertEquals(".", RemotePath.parent("."))
        assertEquals(".", RemotePath.parent(".."))
    }

    @Test
    fun `화면에 보여줄 형태`() {
        assertEquals("/", RemotePath.display("."))
        assertEquals("/공유/a", RemotePath.display("공유/a"))
    }

    @Test
    fun `조각을 이어붙인다`() {
        assertEquals("공유/a/b", RemotePath.join("공유", "a", "b"))
        assertEquals("공유/a", RemotePath.join(".", "공유", "", "a"))
        assertEquals(".", RemotePath.join())
        assertEquals(".", RemotePath.join(".", ""))
    }

    @Test
    fun `이름으로 쓸 수 있는지 본다`() {
        assertTrue(RemotePath.isValidName("보고서.txt"))
        assertTrue(RemotePath.isValidName("이름에 공백"))

        for (bad in listOf("", ".", "..", "a/b", """a\b""", "/", null)) {
            assertFalse("'$bad' 를 받아들였다", RemotePath.isValidName(bad))
        }
    }
}
