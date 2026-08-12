// FileStore 규약 — LocalFileStore 로 검사한다. SAF 구현도 같은 규약을 지켜야 한다
//
// 여기서 고정하는 것은 PROTOCOL.md 6절의 보호 규칙과 각 요청의 동작이다.
// SAF 만의 사정(표시 이름 변형, 원자적 교체 없음)은 기기 실측으로 확인한다.

package com.purehero.netdir.store

import java.io.File
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class FileStoreTest {

    @get:Rule
    val temp = TemporaryFolder()

    private lateinit var shareDir: File
    private lateinit var other: File
    private lateinit var store: FileStore

    @Before
    fun setUp() {
        shareDir = temp.newFolder("사진 모음")
        other = temp.newFolder("공유하지_않는_폴더")
        File(other, "비밀.txt").writeText("보이면 안 된다")

        File(shareDir, "메모.txt").writeText("안녕")
        File(shareDir, "하위").mkdir()
        File(shareDir, "하위/깊이.bin").writeBytes(ByteArray(1000) { it.toByte() })
        File(shareDir, "빈 폴더").mkdir()

        val shares = ShareList()
        shares.add(shareDir.absolutePath, shareDir.name)
        store = LocalFileStore(shares)
    }

    // --- 목록 ---

    @Test
    fun `가상 루트는 공유 폴더들을 보여준다`() {
        val entries = store.list(".")
        assertEquals(listOf("사진 모음"), entries.map { it.name })
        assertTrue(entries[0].isDir)
        assertEquals(0L, entries[0].size)
    }

    @Test
    fun `폴더를 먼저 보여주고 이름순으로 정렬한다`() {
        val entries = store.list("사진 모음")
        assertEquals(listOf("빈 폴더", "하위", "메모.txt"), entries.map { it.name })
    }

    @Test
    fun `디렉터리의 크기는 0 이다`() {
        val dir = store.list("사진 모음").first { it.isDir }
        assertEquals(0L, dir.size)
    }

    @Test
    fun `파일 크기와 시각을 준다`() {
        val file = store.list("사진 모음/하위").first { it.name == "깊이.bin" }
        assertEquals(1000L, file.size)
        assertTrue("mtime 이 비었다", file.mtimeSeconds > 0)
    }

    // --- stat ---

    @Test
    fun `가상 루트의 stat`() {
        val stat = store.stat(".")
        assertTrue(stat.exists)
        assertTrue(stat.isDir)
        assertEquals(0L, stat.size)
    }

    @Test
    fun `없는 항목은 없다고 답한다`() {
        // 예외가 아니라 exists=false 여야 한다. 상대가 덮어쓰기 여부를 물어보는 경로다
        val stat = store.stat("사진 모음/없는파일.txt")
        assertFalse(stat.exists)
    }

    // --- tree ---

    @Test
    fun `재귀 목록은 파일과 빈 폴더를 모두 준다`() {
        val tree = store.tree("사진 모음")

        assertEquals(listOf("메모.txt", "하위/깊이.bin"), tree.files.map { it.path })
        assertEquals(1000L, tree.files.first { it.path.endsWith(".bin") }.size)
        // 빈 폴더는 files 에 안 잡히지만 복사되어야 한다
        assertEquals(listOf("빈 폴더", "하위"), tree.dirs)
    }

    // --- head ---

    @Test
    fun `앞부분만 읽고 전체 크기를 함께 준다`() {
        val head = store.readHead("사진 모음/하위/깊이.bin", 10)

        assertEquals(1000L, head.size)
        assertEquals(10, head.bytes.size)
        assertArrayEquals(ByteArray(10) { it.toByte() }, head.bytes)
    }

    @Test
    fun `한도가 0 이하면 아무것도 읽지 않는다`() {
        // 음수를 그대로 넘기면 파이썬에서 read(-1) 이 되어 파일을 통째로 읽는다.
        // 상한이 우회되므로 0 아래로 내려가면 안 된다
        assertEquals(0, store.readHead("사진 모음/메모.txt", 0).bytes.size)
        assertEquals(0, store.readHead("사진 모음/메모.txt", -1).bytes.size)
    }

    @Test
    fun `폴더는 읽을 수 없다`() {
        denied { store.readHead("사진 모음/하위", 10) }
        denied { store.openRead("사진 모음/하위") }
    }

    // --- 보호 규칙 (PROTOCOL.md 6.2) ---

    @Test
    fun `공유 목록에 없는 이름은 거부한다`() {
        denied { store.list("공유하지_않는_폴더") }
        denied { store.openRead("공유하지_않는_폴더/비밀.txt") }
    }

    @Test
    fun `점점으로 공유 밖에 나가지 못한다`() {
        for (path in listOf(
            "사진 모음/../공유하지_않는_폴더/비밀.txt",
            "사진 모음/하위/../../공유하지_않는_폴더/비밀.txt",
            """사진 모음\..\공유하지_않는_폴더\비밀.txt""",
        )) {
            denied("'$path' 가 통과했다") { store.openRead(path) }
        }
    }

    @Test
    fun `가상 루트는 지우거나 이름을 바꿀 수 없다`() {
        denied { store.delete(".") }
        denied { store.rename(".", "새이름") }
        denied { store.mkdir(".") }
    }

    @Test
    fun `공유 폴더 자체는 지우거나 이름을 바꿀 수 없다`() {
        denied { store.delete("사진 모음") }
        denied { store.rename("사진 모음", "새이름") }
        assertTrue("실제 폴더가 지워졌다", shareDir.isDirectory)
    }

    // --- 만들기 / 지우기 / 이름 바꾸기 ---

    @Test
    fun `폴더를 만든다`() {
        store.mkdir("사진 모음/새 폴더")
        assertTrue(File(shareDir, "새 폴더").isDirectory)
    }

    @Test
    fun `이미 있으면 폴더를 만들지 않는다`() {
        denied { store.mkdir("사진 모음/하위") }
    }

    @Test
    fun `부모가 없으면 폴더를 만들지 않는다`() {
        // mkdir 은 부모를 자동으로 만들지 않는다 (push 와 다르다)
        denied { store.mkdir("사진 모음/없는곳/새 폴더") }
    }

    @Test
    fun `폴더를 재귀로 지운다`() {
        store.delete("사진 모음/하위")
        assertFalse(File(shareDir, "하위").exists())
    }

    @Test
    fun `없는 항목은 지울 수 없다`() {
        denied { store.delete("사진 모음/없는것") }
    }

    @Test
    fun `이름을 바꾼다`() {
        store.rename("사진 모음/메모.txt", "새 메모.txt")
        assertFalse(File(shareDir, "메모.txt").exists())
        assertEquals("안녕", File(shareDir, "새 메모.txt").readText())
    }

    @Test
    fun `이름 바꾸기는 덮어쓰지 않는다`() {
        File(shareDir, "이미있음.txt").writeText("건드리지 마라")
        denied { store.rename("사진 모음/메모.txt", "이미있음.txt") }
        assertEquals("건드리지 마라", File(shareDir, "이미있음.txt").readText())
    }

    @Test
    fun `이름에 경로 조각을 넣을 수 없다`() {
        for (bad in listOf("a/b", """a\b""", "", ".", "..")) {
            denied("'$bad' 를 받아들였다") { store.rename("사진 모음/메모.txt", bad) }
        }
    }

    // --- 받아쓰기 ---

    @Test
    fun `part 로 받고 완료할 때 제 이름으로 바꾼다`() {
        val writer = store.beginWrite("사진 모음/받은것.bin")
        writer.write(byteArrayOf(1, 2, 3), 3)

        val part = File(shareDir, "받은것.bin.part")
        assertTrue("전송 중에는 .part 여야 한다", part.exists())
        assertFalse(File(shareDir, "받은것.bin").exists())

        writer.commit()

        assertFalse(".part 가 남았다", part.exists())
        assertArrayEquals(byteArrayOf(1, 2, 3), File(shareDir, "받은것.bin").readBytes())
    }

    @Test
    fun `중간에 끊기면 반쪽짜리를 남기지 않는다`() {
        val writer = store.beginWrite("사진 모음/받다만것.bin")
        writer.write(byteArrayOf(1, 2, 3), 3)
        writer.abort()

        assertFalse(File(shareDir, "받다만것.bin.part").exists())
        assertFalse(File(shareDir, "받다만것.bin").exists())
    }

    @Test
    fun `받아쓰기는 부모 폴더를 자동으로 만든다`() {
        // push 는 mkdir 과 달리 부모를 만든다
        store.beginWrite("사진 모음/새로/생길/곳/파일.txt").apply {
            write("내용".toByteArray(), "내용".toByteArray().size)
            commit()
        }
        assertEquals("내용", File(shareDir, "새로/생길/곳/파일.txt").readText())
    }

    @Test
    fun `받아쓰기는 있는 파일을 덮어쓴다`() {
        // 파이썬은 os.replace 로 덮어쓴다
        store.beginWrite("사진 모음/메모.txt").apply {
            write("새 내용".toByteArray(), "새 내용".toByteArray().size)
            commit()
        }
        assertEquals("새 내용", File(shareDir, "메모.txt").readText())
    }

    @Test
    fun `공유 밖으로는 받아쓸 수 없다`() {
        denied { store.beginWrite(".") }
        denied { store.beginWrite("사진 모음") }
        denied { store.beginWrite("사진 모음/../공유하지_않는_폴더/침입.txt") }
        assertFalse(File(other, "침입.txt").exists())
    }

    // --- 도구 ---

    private fun denied(why: String = "거부해야 한다", body: () -> Unit) {
        try {
            body()
            fail(why)
        } catch (_: PathDenied) {
        }
    }
}
