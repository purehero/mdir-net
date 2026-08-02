// 세션이 파일에 닿는 유일한 통로 — 원격 경로를 받아 실제 저장소로 옮긴다
//
// 인터페이스로 둔 이유는 두 가지다.
//   1. 폰의 구현(SAF)은 ContentResolver 가 있어야 하므로 JVM 테스트에서 못 쓴다.
//      세션의 핵심(q 규칙, 우편함, 동시 전송)을 검증하려면 파일 뒤편이 필요한데,
//      그것 때문에 기기 테스트로 밀려나면 결국 검사하지 않게 된다.
//   2. 원격 경로 해석과 보호 규칙을 한 곳에 모아두면 새는 곳이 생기지 않는다.
//
// 구현체는 **모든 경로를 반드시 자기 해석기에 통과**시켜야 한다. 가상 루트 밖,
// 공유 목록에 없는 이름, `..` 로 벗어나려는 시도를 전부 막는다.

package net.mdir.android.store

import java.io.InputStream

/** 공유 범위를 벗어났거나 목록에 없는 경로. 세션은 이것을 err 로 회신한다. */
class PathDenied(message: String) : Exception(message)

/** 목록 한 줄. 프로토콜의 entries 항목과 같은 모양이다. */
data class Entry(
    val name: String,
    val isDir: Boolean,
    /** 디렉터리는 0 이다. */
    val size: Long,
    /** 유닉스 epoch **초**. 정수가 아니라 실수다. */
    val mtimeSeconds: Double,
)

data class Stat(val exists: Boolean, val isDir: Boolean, val size: Long)

data class TreeFile(val path: String, val size: Long)

/**
 * 재귀 복사용 목록.
 *
 * `dirs` 가 따로 있는 이유 — **빈 디렉터리는 `files` 에 잡히지 않는데 그것도
 * 복사되어야 하기 때문이다.**
 */
data class Tree(val files: List<TreeFile>, val dirs: List<String>)

/** 뷰어용. `size` 는 파일 전체 크기이고 `bytes` 는 앞부분만이다. */
class Head(val size: Long, val bytes: ByteArray)

/**
 * 받아쓰는 중인 파일.
 *
 * `<이름>.part` 로 쓰고 `commit()` 에서 최종 이름으로 바꾼다. 중간에 끊기면
 * `abort()` 로 지운다. 반쪽짜리 파일이 제 이름을 달고 남지 않게 하는 장치다.
 */
interface PartWriter {
    fun write(bytes: ByteArray, length: Int)
    fun commit()
    fun abort()
}

interface FileStore {

    /** 가상 루트인가. */
    fun isRoot(remotePath: String?): Boolean = RemotePath.isRoot(remotePath)

    /** 가상 루트의 목록. 공유 폴더들을 디렉터리처럼 보여준다. */
    fun rootEntries(): List<Entry>

    fun list(remotePath: String?): List<Entry>

    fun stat(remotePath: String?): Stat

    fun tree(remotePath: String?): Tree

    fun readHead(remotePath: String?, limit: Int): Head

    /** 부모를 자동으로 만들지 않고, 이미 있으면 실패한다. */
    fun mkdir(remotePath: String?)

    /** 디렉터리면 재귀 삭제. 공유 폴더 자체와 가상 루트는 거부한다. */
    fun delete(remotePath: String?)

    /** 덮어쓰지 않는다. 같은 이름이 있으면 거부한다. */
    fun rename(remotePath: String?, newName: String)

    fun openRead(remotePath: String?): InputStream

    /** 받아쓰기를 시작한다. **부모 디렉터리는 자동으로 만든다** (mkdir 과 다르다). */
    fun beginWrite(remotePath: String?): PartWriter
}

/** 목록 정렬 — 디렉터리를 위로, 그 안에서 이름순. 파이썬의 기본 정렬과 같다. */
fun List<Entry>.sortedForDisplay(): List<Entry> =
    sortedWith(compareBy({ !it.isDir }, { it.name.lowercase() }))
