package app.watchman.android.core

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

class ApiException(val status: Int, val code: String, message: String) : Exception(message)

/** The device API (docs/api-contract.md). The OkHttpClient is injected so tests can use a MockWebServer or an interceptor. */
class Api(private val server: String, private val token: String?, private val client: OkHttpClient = defaultClient) {
    companion object {
        val defaultClient: OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(20, TimeUnit.SECONDS).readTimeout(120, TimeUnit.SECONDS).build()
        private val json = "application/json; charset=utf-8".toMediaType()
        private val jpeg = "image/jpeg".toMediaType()

        fun pair(server: String, code: String, deviceName: String, client: OkHttpClient = defaultClient): Pairing {
            val api = Api(server, null, client)
            val body = mapOf("code" to code, "device" to mapOf("name" to deviceName, "platform" to "android", "kind" to "app", "app_version" to "0.1.0"))
            val r = api.call("POST", "/api/v1/pair", gson.toJson(body))
            val m = gson.fromJson(r, Map::class.java)
            return Pairing(server.trimEnd('/'), m["token"] as String, m["device_id"] as String, m["person_id"] as String, m["server_name"] as String)
        }
    }

    private fun call(method: String, path: String, body: String? = null, bytes: ByteArray? = null): String {
        val builder = Request.Builder().url(server.trimEnd('/') + path).header("User-Agent", "watchman-android/0.1.0")
        if (token != null) builder.header("Authorization", "Bearer $token")
        when {
            bytes != null -> builder.method(method, bytes.toRequestBody(jpeg))
            body != null -> builder.method(method, body.toRequestBody(json))
            else -> builder.method(method, null)
        }
        client.newCall(builder.build()).execute().use { res ->
            val text = res.body?.string() ?: ""
            if (!res.isSuccessful) {
                var code = "error"
                var message = res.message
                try {
                    val err = (gson.fromJson(text, Map::class.java)["error"] as? Map<*, *>)
                    code = err?.get("code") as? String ?: code
                    message = err?.get("message") as? String ?: message
                } catch (_: Exception) { }
                throw ApiException(res.code, code, message)
            }
            return text
        }
    }

    fun heartbeat(body: Map<String, Any?>): HeartbeatResponse =
        gson.fromJson(call("POST", "/api/v1/heartbeat", gson.toJson(body)), HeartbeatResponse::class.java)

    fun policy(): Policy = gson.fromJson(call("GET", "/api/v1/policy"), Policy::class.java)

    /** Cheap: version and pause only, no database writes on the server. */
    fun policyVersion(): HeartbeatResponse = gson.fromJson(call("GET", "/api/v1/policy/version"), HeartbeatResponse::class.java)

    fun events(events: List<WatchmanEvent>): EventsResponse =
        gson.fromJson(call("POST", "/api/v1/events", gson.toJson(mapOf("events" to events))), EventsResponse::class.java)

    fun uploadImage(eventId: String, jpegBytes: ByteArray) { call("PUT", "/api/v1/events/$eventId/image", bytes = jpegBytes) }

    /** The server's bigger model on one crop; scored in memory, nothing stored (ADR-022). */
    fun judge(jpegBytes: ByteArray): JudgeResponse =
        gson.fromJson(call("POST", "/api/v1/judge", bytes = jpegBytes), JudgeResponse::class.java)

    fun manifest(): ModelsManifest = gson.fromJson(call("GET", "/api/v1/models/manifest"), ModelsManifest::class.java)

    fun modelBytes(path: String): ByteArray {
        val builder = Request.Builder().url(server.trimEnd('/') + path)
        if (token != null) builder.header("Authorization", "Bearer $token")
        client.newCall(builder.build()).execute().use { res ->
            if (!res.isSuccessful) throw ApiException(res.code, "error", res.message)
            return res.body!!.bytes()
        }
    }

    fun unlock(code: String): String {
        val r = gson.fromJson(call("POST", "/api/v1/unlock", gson.toJson(mapOf("code" to code))), Map::class.java)
        return r["paused_until"] as String
    }

    fun requestUnlock(minutes: Int, reason: String) {
        call("POST", "/api/v1/unlock/request", gson.toJson(mapOf("minutes" to minutes, "reason" to reason)))
    }
}
