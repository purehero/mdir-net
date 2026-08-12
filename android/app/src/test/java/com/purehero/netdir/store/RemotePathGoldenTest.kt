// 경로 해석 차분 검증 — 파이썬 shares.py 가 낸 결과와 한 글자도 갈리면 안 된다
//
// 경로 해석은 보안 경계다. 여기가 어긋나면 공유 밖이 열리거나 멀쩡한 경로가 막힌다.
// 손으로 쓴 기대값이 아니라 **참조 구현이 실제로 낸 값**과 맞춰야 의미가 있다.
//
// 벡터는 tools/gen-golden.py 가 만든다.

package com.purehero.netdir.store

import org.json.JSONArray
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class RemotePathGoldenTest {

    @Test
    fun `파이썬과 똑같이 해석한다`() {
        val rows = golden()
        assertTrue("벡터가 비었다", rows.length() > 0)

        for (index in 0 until rows.length()) {
            val row = rows.getJSONObject(index)
            val input = row.getString("input")

            val expectedParts = row.getJSONArray("parts").let { array ->
                (0 until array.length()).map { array.getString(it) }
            }

            assertEquals("split($input)", expectedParts, RemotePath.split(input))
            assertEquals("normalize($input)", row.getString("normalized"), RemotePath.normalize(input))
            assertEquals("parent($input)", row.getString("parent"), RemotePath.parent(input))
            assertEquals("display($input)", row.getString("display"), RemotePath.display(input))
            assertEquals("isRoot($input)", row.getBoolean("isRoot"), RemotePath.isRoot(input))
        }
    }

    /**
     * 벡터가 실제로 위험한 입력을 담고 있는지 확인한다.
     *
     * 이게 없으면 누가 PATHS 목록에서 `..` 을 다 지워도 위 테스트는 조용히 통과한다.
     */
    @Test
    fun `벡터가 탈출 시도를 포함한다`() {
        val inputs = golden().let { rows ->
            (0 until rows.length()).map { rows.getJSONObject(it).getString("input") }
        }

        assertTrue("`..` 이 든 입력이 없다", inputs.any { it.contains("..") })
        assertTrue("백슬래시가 든 입력이 없다", inputs.any { it.contains("\\") })
        assertTrue("겹친 슬래시가 든 입력이 없다", inputs.any { it.contains("//") })

        // 루트를 벗어나려다 붙잡힌 입력이 적어도 하나는 있어야 한다.
        // (`...` 은 `..` 을 담고 있지만 멀쩡한 이름이므로 이 조건에 걸리지 않는다)
        assertTrue(
            "루트에서 멈춰야 하는 탈출 시도가 벡터에 없다",
            inputs.any { it.contains("..") && RemotePath.split(it).isEmpty() },
        )
    }

    private fun golden() = JSONArray(
        checkNotNull(javaClass.classLoader!!.getResourceAsStream("golden/paths.json")) {
            "golden/paths.json 이 없습니다. tools/gen-golden.py 를 실행하세요"
        }.use { it.readBytes().toString(Charsets.UTF_8) },
    )
}
