package app.watchman.android.core

import java.net.URI
import java.net.URLDecoder

/**
 * A pairing hand-off from a QR code or a link: the server to pair with and the code. Accepts the
 * portal address the Devices page's QR code holds (`https://<server>/pair?code=K7Q4MZP2D`) and the
 * app link its /pair page opens (`watchman://pair?server=<url>&code=K7Q4MZP2D`). It only fills in
 * the pairing form; nothing pairs until the person taps Pair.
 */
data class PairLink(val server: String, val code: String) {
    companion object {
        private const val ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

        /** Nine code characters in three dashed groups, from whatever was typed or scanned. */
        fun formatCode(text: String): String {
            val chars = text.uppercase().filter { it in ALPHABET }.take(9)
            return chars.chunked(3).joinToString("-")
        }

        fun parse(text: String?): PairLink? {
            val uri = try { URI(text?.trim() ?: return null) } catch (_: Exception) { return null }
            val query = (uri.rawQuery ?: "").split("&").mapNotNull {
                val i = it.indexOf('=')
                if (i <= 0) null else it.substring(0, i) to URLDecoder.decode(it.substring(i + 1), "UTF-8")
            }.toMap()
            val code = formatCode(query["code"] ?: return null)
            if (code.length != 11) return null
            val server = when {
                uri.scheme == "watchman" && uri.host == "pair" -> query["server"] ?: return null
                (uri.scheme == "https" || uri.scheme == "http") && uri.path == "/pair" ->
                    "${uri.scheme}://${uri.rawAuthority}"
                else -> return null
            }
            if (!server.startsWith("https://") && !server.startsWith("http://")) return null
            return PairLink(server.trimEnd('/'), code)
        }
    }

    fun toMap(): Map<String, String> = mapOf("server" to server, "code" to code)
}
