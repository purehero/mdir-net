// 공유 목록을 기기에 남기고, 고른 트리에 대한 영구 권한을 받아둔다
//
// 목록 자체의 규칙(이름 겹침 처리, 잘못된 항목 버리기)은 ShareList 가 갖고 있다.
// 여기는 안드로이드에 기대는 부분 — 권한 확보와 저장 — 만 맡는다.

package net.mdir.android.store

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.DocumentsContract
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

class SharesStore(context: Context) {

    private val app = context.applicationContext
    private val prefs = app.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    val list: ShareList = ShareList.fromJson(prefs.getString(KEY, null))

    private val _shares = MutableStateFlow(list.all())

    /** 화면이 지켜본다. 목록이 바뀌면 곧바로 반영된다. */
    val shares: StateFlow<List<ShareRef>> = _shares.asStateFlow()

    /**
     * 고른 트리를 목록에 넣는다.
     *
     * `takePersistableUriPermission` 을 받아야 앱을 다시 켰을 때도 접근할 수 있다.
     * 받지 못하면 넣지 않는다 — 다음 실행에서 조용히 실패하는 목록을 남기는 것보다
     * 지금 실패하는 편이 낫다.
     */
    fun add(treeUri: Uri): ShareRef? {
        val flags = Intent.FLAG_GRANT_READ_URI_PERMISSION or
            Intent.FLAG_GRANT_WRITE_URI_PERMISSION
        try {
            app.contentResolver.takePersistableUriPermission(treeUri, flags)
        } catch (_: SecurityException) {
            return null
        }

        val share = list.add(treeUri.toString(), displayName(treeUri))
        persist()
        return share
    }

    fun remove(name: String) {
        val share = list.get(name) ?: return
        list.remove(name)
        persist()

        // 다른 공유가 같은 트리를 쓰고 있지 않을 때만 권한을 놓는다
        if (list.all().none { it.key == share.key }) {
            try {
                app.contentResolver.releasePersistableUriPermission(
                    Uri.parse(share.key),
                    Intent.FLAG_GRANT_READ_URI_PERMISSION or
                        Intent.FLAG_GRANT_WRITE_URI_PERMISSION,
                )
            } catch (_: Exception) {
            }
        }
    }

    fun store(resolver: android.content.ContentResolver = app.contentResolver) =
        SafFileStore(resolver, list)

    private fun persist() {
        prefs.edit().putString(KEY, list.toJson()).apply()
        _shares.value = list.all()
    }

    /**
     * 트리 루트의 표시 이름. 못 읽으면 URI 마지막 조각에서 만들어낸다.
     *
     * `primary:Download/사진` 같은 문서 id 가 오므로 `/` 뒤쪽만 쓴다. 그대로 두면
     * 공유 이름에 `/` 가 들어가 경로 조각과 헷갈린다.
     */
    private fun displayName(treeUri: Uri): String {
        val projection = arrayOf(DocumentsContract.Document.COLUMN_DISPLAY_NAME)
        val rootId = runCatching { DocumentsContract.getTreeDocumentId(treeUri) }.getOrNull()
        val docUri = rootId?.let {
            runCatching { DocumentsContract.buildDocumentUriUsingTree(treeUri, it) }.getOrNull()
        }

        if (docUri != null) {
            val name = try {
                app.contentResolver.query(docUri, projection, null, null, null)?.use {
                    if (it.moveToFirst() && !it.isNull(0)) it.getString(0) else null
                }
            } catch (_: Exception) {
                null
            }
            if (!name.isNullOrBlank()) return name
        }

        return rootId?.substringAfterLast('/')?.takeIf { it.isNotBlank() } ?: "공유"
    }

    private companion object {
        const val PREFS = "mdirnet"
        const val KEY = "shares"
    }
}
