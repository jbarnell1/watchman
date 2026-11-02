using System.Net;
using System.Text;
using System.Text.Json;
using Watchman.Core;
using Xunit;

namespace Watchman.Core.Tests;

public class StoreAndApiTests
{
    private static WatchmanEvent Ev(string id) => new() { Id = id, OccurredAt = "2026-11-01T00:00:00Z", App = "chrome.exe" };

    [Fact]
    public async Task QueueKeepsWhatTheServerDidNotTake()
    {
        var q = new EventQueue(new MemoryStore());
        q.Push(Ev("a")); q.Push(Ev("b")); q.Push(Ev("c"));
        var wanted = await q.FlushAsync(batch =>
        {
            Assert.Equal(new[] { "a", "b", "c" }, batch.Select(e => e.Id));
            return Task.FromResult(new EventsResponse(new() { "a" }, new() { "b" }, new() { "a" }));
        });
        Assert.Equal(new[] { "a" }, wanted);
        Assert.Equal(new[] { "c" }, q.All().Select(e => e.Id));
    }

    [Fact]
    public void QueueIsCapped()
    {
        var q = new EventQueue(new MemoryStore());
        for (var i = 0; i < EventQueue.MaxQueue + 5; i++) q.Push(Ev(i.ToString()));
        Assert.Equal(EventQueue.MaxQueue, q.Count);
        Assert.Equal("5", q.All()[0].Id);
    }

    [Fact]
    public void ProtectedStoreRoundTripsOnThisMachine()
    {
        var dir = Path.Combine(Path.GetTempPath(), "watchman-test-" + Guid.NewGuid());
        var store = new ProtectedFileStore(dir);
        store.WriteText("pairing.json", "{\"token\":\"secret\"}");
        var raw = File.ReadAllText(Path.Combine(dir, "pairing.json"));
        Assert.DoesNotContain("secret", raw);
        Assert.Equal("{\"token\":\"secret\"}", store.ReadText("pairing.json"));
        store.Delete("pairing.json");
        Assert.Null(store.ReadText("pairing.json"));
        Directory.Delete(dir, true);
    }

    private sealed class FakeHandler(Func<HttpRequestMessage, (HttpStatusCode, string)> answer) : HttpMessageHandler
    {
        public List<HttpRequestMessage> Requests { get; } = new();
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
        {
            Requests.Add(request);
            if (request.Content is not null) await request.Content.LoadIntoBufferAsync();
            var (status, body) = answer(request);
            return new HttpResponseMessage(status) { Content = new StringContent(body, Encoding.UTF8, "application/json") };
        }
    }

    [Fact]
    public async Task PairThenPolicyCarriesTheToken()
    {
        var handler = new FakeHandler(req => req.RequestUri!.AbsolutePath switch
        {
            "/api/v1/pair" => (HttpStatusCode.OK, "{\"device_id\":\"d\",\"token\":\"tok\",\"person_id\":\"p\",\"server_name\":\"Example household\",\"api_version\":1}"),
            "/api/v1/policy" => (HttpStatusCode.OK, "{\"version\":3,\"thresholds\":{\"cover\":0.8,\"flag\":0.6,\"detector_cover\":0.55},\"strict_mode\":true,\"risky_apps\":[],\"blocked_apps\":[],\"protected_browser\":\"org.mozilla.firefox\",\"sample_interval_s\":180,\"whole_screen_interval_s\":5,\"paused_until\":null,\"known_bad_hashes\":[\"9f3a0c1e5b7d2a46\"]}"),
            _ => (HttpStatusCode.NotFound, "{\"error\":{\"code\":\"not_found\",\"message\":\"no\"}}"),
        });
        var pairing = await Api.PairAsync("https://watchman.test/", "K7Q-4MZ-P2D", "family-pc", handler);
        Assert.Equal("tok", pairing.Token);
        var pairBody = await handler.Requests[0].Content!.ReadAsStringAsync();
        Assert.Contains("\"platform\":\"windows\"", pairBody);
        using var api = new Api(pairing.Server, pairing.Token, handler);
        var policy = await api.PolicyAsync();
        Assert.Equal(3, policy.Version);
        Assert.Equal(0.55, policy.Thresholds.DetectorCover);
        Assert.Equal("Bearer tok", handler.Requests[1].Headers.Authorization!.ToString());
        Assert.False(policy.IsPaused(new FakeClock()));
    }

    [Fact]
    public async Task ServerErrorsCarryTheirCode()
    {
        var handler = new FakeHandler(_ => (HttpStatusCode.BadRequest, "{\"error\":{\"code\":\"unlock_code_invalid\",\"message\":\"nope\"}}"));
        using var api = new Api("https://w", "t", handler);
        var ex = await Assert.ThrowsAsync<ApiException>(() => api.UnlockAsync("000-000"));
        Assert.Equal("unlock_code_invalid", ex.Code);
        Assert.Equal(400, ex.Status);
    }

    [Fact]
    public void EventsSerialiseWithSnakeCase()
    {
        var e = new WatchmanEvent { Id = "x", OccurredAt = "2026-11-01T00:00:00Z", HasImage = true, Hash = "ab" };
        var json = JsonSerializer.Serialize(e, Json.Options);
        Assert.Contains("\"occurred_at\":\"2026-11-01T00:00:00Z\"", json);
        Assert.Contains("\"has_image\":true", json);
    }

    [Fact]
    public void PipeMessagesRoundTrip()
    {
        var line = Wire.Encode<HelperMessage>(new FindingMessage("ab", 0.9, new() { "X" }, 1, 2, 3, 4, 100, 200, "chrome.exe", null, "AAA=", false));
        Assert.Contains("\"type\":\"finding\"", line);
        var back = Wire.Decode<HelperMessage>(line);
        var f = Assert.IsType<FindingMessage>(back);
        Assert.Equal("chrome.exe", f.App);
        var cfg = Wire.Decode<ServiceMessage>(Wire.Encode<ServiceMessage>(new ConfigMessage(true, "home", null, null, null, null)));
        Assert.True(Assert.IsType<ConfigMessage>(cfg).Paired);
    }
}
