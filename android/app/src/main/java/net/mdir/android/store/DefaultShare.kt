// 기본 공유 폴더 — 다운로드 안의 `mdir-net` 을 앱이 미리 만들어 둔다
//
// 다운로드 폴더 **자체**는 안드로이드 11 부터 트리로 고를 수 없다. 그래서 그 안에
// 폴더가 하나 있어야 하는데, 지금까지는 사용자가 선택창에서 직접 만들어야 했다.
// 첫 실행에서 공유 목록이 비고 `켜기` 가 비활성인 채로 막히던 지점이다.

package net.mdir.android.store

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.Build
import android.provider.DocumentsContract
import android.provider.MediaStore

/** 다운로드 **하위**여야 한다. 다운로드 자체는 트리로 고를 수 없다. */
private const val RELATIVE_PATH = "Download/mdir-net"

/** 폴더만 필요하다. 넣자마자 지우므로 남지 않는다. */
private const val STUB_NAME = ".mdirnet"

private const val EXTERNAL_STORAGE = "com.android.externalstorage.documents"

/**
 * 기본 폴더를 앱이 만들어 쓰는가.
 *
 * API 28 이하는 아니다 — `MediaStore.Downloads` 가 없고, 그 버전에서는 다운로드
 * 폴더 자체를 트리로 고를 수 있어 애초에 필요하지 않다. 안내 문구도 이것으로 갈린다.
 */
val USES_DEFAULT_FOLDER: Boolean = Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q

/**
 * 선택창을 열 위치.
 *
 * 없는 폴더를 가리키면 선택창이 기본 위치에서 열린다 — 손해는 없다.
 */
val DEFAULT_SHARE_PICKER_URI: Uri = DocumentsContract.buildDocumentUri(
    EXTERNAL_STORAGE,
    if (USES_DEFAULT_FOLDER) "primary:$RELATIVE_PATH" else "primary:Download",
)

/**
 * `Download/mdir-net` 이 없으면 만든다. 이미 있으면 아무 일도 일어나지 않는다.
 *
 * 다운로드 폴더에는 트리 권한이 없어서 `DocumentsContract.createDocument` 로는 못
 * 만든다. `MediaStore` 에 `RELATIVE_PATH` 를 주고 파일을 넣으면 중간 폴더가 만들어
 * 지므로, 더미를 넣고 곧바로 지운다. 파일은 사라지고 폴더는 남는다.
 *
 * API 28 이하는 하지 않는다. `MediaStore.Downloads` 가 API 29 부터이고, 그 아래
 * 버전에서는 다운로드 폴더 자체를 트리로 고를 수 있어 애초에 필요하지 않다.
 *
 * 실패해도 그냥 넘어간다. 선택창은 어차피 열리고, 사용자가 예전처럼 직접 폴더를
 * 만들면 된다. 여기서 막아설 이유가 없다.
 */
fun ensureDefaultShareFolder(context: Context) {
    if (!USES_DEFAULT_FOLDER) return

    val resolver = context.contentResolver
    val values = ContentValues().apply {
        put(MediaStore.Downloads.DISPLAY_NAME, STUB_NAME)
        put(MediaStore.Downloads.RELATIVE_PATH, RELATIVE_PATH)
        put(MediaStore.Downloads.MIME_TYPE, "application/octet-stream")
    }

    val uri = runCatching {
        resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
    }.getOrNull() ?: return

    // insert 만으로 디스크에 자리가 잡히지 않을 수 있다. 한 번 열었다 닫아
    // 파일과 중간 폴더가 실제로 생기는 것을 확실히 한다
    runCatching { resolver.openOutputStream(uri)?.close() }
    runCatching { resolver.delete(uri, null, null) }
}
