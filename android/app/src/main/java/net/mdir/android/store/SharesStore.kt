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

    /**
     * 작업 폴더 — 브라우저의 `내 폰` 뿌리다. 공유 폴더들은 이 안에 둔다.
     *
     * 공유 목록과 따로 두는 이유는 **상대에게 열어주는 것과 다르기 때문**이다.
     * 상대는 공유 목록만 보고, 나는 작업 폴더 전체를 본다.
     */
    var workspace: Uri? = prefs.getString(WORKSPACE, null)?.let { Uri.parse(it) }
        private set

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
    fun add(treeUri: Uri, sub: List<String> = emptyList()): ShareRef? {
        val flags = Intent.FLAG_GRANT_READ_URI_PERMISSION or
            Intent.FLAG_GRANT_WRITE_URI_PERMISSION
        try {
            app.contentResolver.takePersistableUriPermission(treeUri, flags)
        } catch (_: SecurityException) {
            return null
        }

        val key = ShareKey.make(treeUri.toString(), sub)
        val share = list.add(key, sub.lastOrNull() ?: displayName(treeUri))
        persist()
        return share
    }

    /** 작업 폴더를 정한다. 여기 권한은 `add` 가 이미 받아둔 것을 쓴다. */
    fun setWorkspace(treeUri: Uri) {
        workspace = treeUri
        prefs.edit().putString(WORKSPACE, treeUri.toString()).apply()
    }

    fun remove(name: String) {
        val share = list.get(name) ?: return
        list.remove(name)
        persist()

        // 다른 공유가 같은 **트리**를 쓰고 있지 않을 때만 권한을 놓는다. 하위 경로
        // 공유가 여럿이면 트리는 하나이므로 key 가 아니라 트리로 봐야 한다.
        // 작업 폴더도 같은 트리를 쓰므로 그것이 살아 있으면 놓지 않는다
        val tree = ShareKey.tree(share.key)
        val treeInUse = list.all().any { ShareKey.tree(it.key) == tree } ||
            workspace?.toString() == tree
        if (!treeInUse) {
            try {
                app.contentResolver.releasePersistableUriPermission(
                    Uri.parse(tree),
                    Intent.FLAG_GRANT_READ_URI_PERMISSION or
                        Intent.FLAG_GRANT_WRITE_URI_PERMISSION,
                )
            } catch (_: Exception) {
            }
        }
    }

    fun store(resolver: android.content.ContentResolver = app.contentResolver) =
        SafFileStore(resolver, list)

    /**
     * 브라우저의 `내 폰` 쪽 저장소. 작업 폴더 하나만 담은 목록으로 만든다.
     *
     * 세션이 쓰는 `store()` 와 **다른 것**이어야 한다. 상대는 공유 목록만 보고
     * 나는 작업 폴더를 본다. 하나로 합치면 그 구분이 사라진다.
     */
    fun localStore(resolver: android.content.ContentResolver = app.contentResolver): FileStore? =
        workspace?.let {
            SafFileStore(resolver, ShareList(listOf(ShareRef(WORKSPACE_NAME, it.toString()))))
        }

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
        const val WORKSPACE = "workspace"
    }
}
