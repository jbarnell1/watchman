using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;

namespace Watchman.Core;

public sealed class ApiException(int status, string code, string message) : Exception(message)
{
    public int Status { get; } = status;
    public string Code { get; } = code;
}

/// <summary>The device API. Takes an HttpMessageHandler so tests use a fake.</summary>
public sealed class Api : IDisposable
{
    private readonly HttpClient _http;

    public Api(string server, string? token, HttpMessageHandler? handler = null)
    {
        _http = handler is null ? new HttpClient() : new HttpClient(handler);
        _http.BaseAddress = new Uri(server.TrimEnd('/') + "/");
        _http.Timeout = TimeSpan.FromSeconds(60);
        if (token is not null) _http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
        _http.DefaultRequestHeaders.UserAgent.ParseAdd("watchman-windows/0.1.0");
    }

    private static async Task<T> Read<T>(HttpResponseMessage res)
    {
        if (!res.IsSuccessStatusCode)
        {
            var code = "error";
            var message = res.ReasonPhrase ?? "request failed";
            try
            {
                using var doc = JsonDocument.Parse(await res.Content.ReadAsStringAsync());
                if (doc.RootElement.TryGetProperty("error", out var err))
                {
                    code = err.GetProperty("code").GetString() ?? code;
                    message = err.GetProperty("message").GetString() ?? message;
                }
            }
            catch (JsonException) { }
            throw new ApiException((int)res.StatusCode, code, message);
        }
        return (await res.Content.ReadFromJsonAsync<T>(Json.Options))!;
    }

    public static async Task<Pairing> PairAsync(string server, string code, string deviceName, HttpMessageHandler? handler = null)
    {
        using var api = new Api(server, null, handler);
        var body = new { code, device = new { name = deviceName, platform = "windows", kind = "service", app_version = "0.1.0" } };
        var res = await api._http.PostAsJsonAsync("api/v1/pair", body);
        var r = await Read<JsonElement>(res);
        return new Pairing(server, r.GetProperty("token").GetString()!, r.GetProperty("device_id").GetString()!,
            r.GetProperty("person_id").GetString()!, r.GetProperty("server_name").GetString()!);
    }

    public async Task<HeartbeatResponse> HeartbeatAsync(object body) =>
        await Read<HeartbeatResponse>(await _http.PostAsJsonAsync("api/v1/heartbeat", body, Json.Options));

    public async Task<Policy> PolicyAsync() => await Read<Policy>(await _http.GetAsync("api/v1/policy"));

    public async Task<EventsResponse> EventsAsync(IEnumerable<WatchmanEvent> events) =>
        await Read<EventsResponse>(await _http.PostAsJsonAsync("api/v1/events", new { events }, Json.Options));

    public async Task UploadImageAsync(string eventId, byte[] jpeg)
    {
        using var content = new ByteArrayContent(jpeg);
        content.Headers.ContentType = new MediaTypeHeaderValue("image/jpeg");
        var res = await _http.PutAsync($"api/v1/events/{eventId}/image", content);
        if (!res.IsSuccessStatusCode) await Read<JsonElement>(res);
    }

    public async Task<ModelsManifest> ManifestAsync() => await Read<ModelsManifest>(await _http.GetAsync("api/v1/models/manifest"));

    public async Task<byte[]> ModelBytesAsync(string path)
    {
        var res = await _http.GetAsync(path.TrimStart('/'));
        if (!res.IsSuccessStatusCode) await Read<JsonElement>(res);
        return await res.Content.ReadAsByteArrayAsync();
    }

    public async Task<string> UnlockAsync(string code)
    {
        var r = await Read<JsonElement>(await _http.PostAsJsonAsync("api/v1/unlock", new { code }));
        return r.GetProperty("paused_until").GetString()!;
    }

    public async Task RequestUnlockAsync(int minutes, string reason)
    {
        var res = await _http.PostAsJsonAsync("api/v1/unlock/request", new { minutes, reason });
        if (!res.IsSuccessStatusCode) await Read<JsonElement>(res);
    }

    public void Dispose() => _http.Dispose();
}
