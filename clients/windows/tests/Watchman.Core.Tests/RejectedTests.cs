using System.Net;
using System.Text;
using Microsoft.Extensions.Logging.Abstractions;
using Watchman.Core;
using Watchman.Service;
using Xunit;

namespace Watchman.Core.Tests;

/// <summary>A 401 must never switch protection off (fail closed): a fake server could otherwise
/// unpair the PC without anyone hearing.</summary>
public class RejectedTests
{
    private sealed class Server(Func<string, (HttpStatusCode, string)> answer) : HttpMessageHandler
    {
        public List<string> Paths { get; } = new();
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
        {
            var path = request.RequestUri!.AbsolutePath;
            Paths.Add(path);
            var (status, body) = answer(path);
            return Task.FromResult(new HttpResponseMessage(status) { Content = new StringContent(body, Encoding.UTF8, "application/json") });
        }
    }

    private const string Unauthorized = "{\"error\":{\"code\":\"unauthorized\",\"message\":\"unknown token\"}}";

    private static ServiceState Paired(Server server)
    {
        var state = new ServiceState(new MemoryStore(), new FakeClock()) { ApiFactory = p => new Api(p.Server, p.Token, server) };
        state.Pairing = new Pairing("https://watchman.test", "tok", "d", "p", "Example household");
        state.Policy = Policy.Defaults with { Version = 3 };
        state.Queue.Push(new WatchmanEvent { Id = "e1", OccurredAt = "2026-11-01T00:00:00Z" });
        return state;
    }

    [Fact]
    public async Task A401KeepsThePairingPolicyAndQueue()
    {
        var server = new Server(_ => (HttpStatusCode.Unauthorized, Unauthorized));
        var state = Paired(server);
        var worker = new ServiceWorker(state, NullLogger<ServiceWorker>.Instance);

        await worker.HeartbeatAsync(force: true, CancellationToken.None);

        Assert.Contains("/api/v1/heartbeat", server.Paths);
        Assert.NotNull(state.Pairing);
        Assert.Equal("tok", state.Pairing!.Token);
        Assert.Equal(3, state.Policy!.Version);
        Assert.Equal(1, state.Queue.Count);
        Assert.True(state.Rejected);
        Assert.True(state.Config().Paired);

        // Heartbeats go on: the next one is still sent with the same token.
        server.Paths.Clear();
        await worker.HeartbeatAsync(force: true, CancellationToken.None);
        Assert.Contains("/api/v1/heartbeat", server.Paths);
    }

    [Fact]
    public async Task AnAcceptedHeartbeatOrANewPairingClearsRejected()
    {
        var accept = false;
        var server = new Server(path => !accept ? (HttpStatusCode.Unauthorized, Unauthorized) : path switch
        {
            "/api/v1/heartbeat" => (HttpStatusCode.OK, "{\"policy_version\":3,\"paused_until\":null,\"server_time\":\"2026-11-01T12:00:00Z\"}"),
            "/api/v1/models/manifest" => (HttpStatusCode.OK, "{\"version\":1,\"models\":[]}"),
            _ => (HttpStatusCode.NotFound, "{\"error\":{\"code\":\"not_found\",\"message\":\"no\"}}"),
        });
        var state = Paired(server);
        var worker = new ServiceWorker(state, NullLogger<ServiceWorker>.Instance);
        await worker.HeartbeatAsync(force: true, CancellationToken.None);
        Assert.True(state.Rejected);

        accept = true;
        await worker.HeartbeatAsync(force: true, CancellationToken.None);
        Assert.False(state.Rejected);

        state.Rejected = true;
        state.Pairing = new Pairing("https://watchman.test", "new-token", "d2", "p", "Example household");
        Assert.False(state.Rejected);
        Assert.Equal("new-token", state.Pairing!.Token);
    }

    [Fact]
    public void WithNoPolicyTheHelperGetsTheDefaults()
    {
        var state = new ServiceState(new MemoryStore(), new FakeClock());
        state.Pairing = new Pairing("https://watchman.test", "tok", "d", "p", "Example household");
        var config = state.Config();
        Assert.NotNull(config.Policy);
        Assert.Same(Policy.Defaults, config.Policy);
        Assert.False(config.Policy!.IsPaused(new FakeClock()));
    }
}
