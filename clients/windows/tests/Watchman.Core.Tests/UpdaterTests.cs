using System.Net;
using System.Security.Cryptography;
using System.Text;
using Microsoft.Extensions.Logging.Abstractions;
using Watchman.Core;
using Watchman.Service;
using Xunit;

namespace Watchman.Core.Tests;

/// <summary>Updates from the household server (ADR-061): only newer, only what matches its SHA-256,
/// never every heartbeat, and no false "protection off" while the service or helper restarts.</summary>
public class UpdaterTests : IDisposable
{
    private const string Name = "watchman-windows-0.3.0-x64.msi";
    private static readonly byte[] Msi = Encoding.ASCII.GetBytes("an msi");
    private static readonly string Sum = Convert.ToHexStringLower(SHA256.HashData(Msi));
    private readonly string _dir = Path.Combine(Path.GetTempPath(), "watchman-updates-" + Guid.NewGuid().ToString("N"));

    public void Dispose()
    {
        if (Directory.Exists(_dir)) Directory.Delete(_dir, recursive: true);
    }

    private sealed class Server : HttpMessageHandler
    {
        public List<string> Paths { get; } = new();
        public string Heartbeat { get; set; } = "{\"policy_version\":3,\"paused_until\":null,\"server_time\":\"2026-11-01T12:00:00Z\"}";
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
        {
            var path = request.RequestUri!.AbsolutePath;
            Paths.Add(path);
            var res = path switch
            {
                "/downloads/" + Name => new HttpResponseMessage(HttpStatusCode.OK) { Content = new ByteArrayContent(Msi) },
                "/api/v1/heartbeat" => Json(Heartbeat),
                "/api/v1/policy" => Json("{\"version\":3}"),
                _ => new HttpResponseMessage(HttpStatusCode.NotFound) { Content = new StringContent("{}") },
            };
            return Task.FromResult(res);
        }

        private static HttpResponseMessage Json(string body) =>
            new(HttpStatusCode.OK) { Content = new StringContent(body, Encoding.UTF8, "application/json") };
    }

    private static UpdateOffer Offer(string version = "0.3.0", string sha = "", string name = Name, string variant = "x64") =>
        new(version, [new UpdateFile(name, variant, sha == "" ? Sum : sha, Msi.Length, "/downloads/" + name)]);

    private (ServiceState, Server, Updater, List<string>) Setup()
    {
        var server = new Server();
        var state = new ServiceState(new MemoryStore(), new FakeClock()) { ApiFactory = p => new Api(p.Server, p.Token, server) };
        state.Pairing = new Pairing("https://watchman.test", "tok", "d", "p", "Example household");
        var installed = new List<string>();
        var updater = new Updater(state, NullLogger.Instance)
        {
            Directory = _dir, LockDown = false, CurrentVersion = "0.2.2", Install = (msi, _) => installed.Add(msi),
        };
        return (state, server, updater, installed);
    }

    [Fact]
    public void VersionsOrderLikeTheServer()
    {
        Assert.True(AppVersion.IsNewer("0.2.3", "0.2.2"));
        Assert.True(AppVersion.IsNewer("0.3.0", "0.2.10"));
        Assert.True(AppVersion.IsNewer("0.2.2", "0.2.2-rc.1"));
        Assert.True(AppVersion.IsNewer("0.2.2", "0.0.0-pr.6"));
        Assert.False(AppVersion.IsNewer("0.2.2-rc.1", "0.2.2"));
        Assert.False(AppVersion.IsNewer("0.2.2", "0.2.2"));
        Assert.False(AppVersion.IsNewer("0.2.1", "0.2.2"));
        Assert.False(AppVersion.IsNewer("latest", "0.2.2"));
    }

    [Fact]
    public async Task ANewerMsiThatMatchesIsInstalledOnceNotEveryHeartbeat()
    {
        var (state, server, updater, installed) = Setup();
        using var api = state.Api()!;

        Assert.True(await updater.ConsiderAsync(api, Offer()));
        Assert.Equal([Path.Combine(_dir, Name)], installed);
        Assert.Equal(Msi, File.ReadAllBytes(installed[0]));

        Assert.False(await updater.ConsiderAsync(api, Offer()));   // the install restarts this service; a failed one waits
        Assert.Single(installed);
        ((FakeClock)state.Clock).Advance(Updater.RetryAfter);
        Assert.True(await updater.ConsiderAsync(api, Offer()));
        Assert.Equal(2, installed.Count);
    }

    [Fact]
    public async Task NothingIsInstalledThatDoesNotMatchItsHashOrIsNotNewer()
    {
        var (state, server, updater, installed) = Setup();
        using var api = state.Api()!;

        Assert.False(await updater.ConsiderAsync(api, Offer(sha: new string('0', 64))));
        Assert.False(await updater.ConsiderAsync(api, Offer(version: "0.2.2")));
        Assert.False(await updater.ConsiderAsync(api, Offer(version: "0.2.1")));
        Assert.False(await updater.ConsiderAsync(api, Offer(version: "0.4.0", variant: "arm64")));
        Assert.False(await updater.ConsiderAsync(api, Offer(version: "0.4.1", name: "..\\evil.msi")));
        Assert.False(await updater.ConsiderAsync(api, null));
        Assert.Empty(installed);
        Assert.False(File.Exists(Path.Combine(_dir, Name)));
    }

    [Fact]
    public async Task AHeartbeatThatOffersAnUpdateStartsTheInstall()
    {
        var (state, server, _, installed) = Setup();
        state.HelperSeenAt = state.HelperStateAt = state.Clock.Now;
        server.Heartbeat = "{\"policy_version\":3,\"paused_until\":null,\"server_time\":\"2026-11-01T12:00:00Z\"," +
            $"\"update\":{{\"version\":\"0.3.0\",\"files\":[{{\"name\":\"{Name}\",\"variant\":\"x64\",\"sha256\":\"{Sum}\",\"size\":{Msi.Length},\"url\":\"/downloads/{Name}\"}}]}}}}";
        var worker = new ServiceWorker(state, NullLogger<ServiceWorker>.Instance)
        {
            Updater = new Updater(state, NullLogger.Instance) { Directory = _dir, LockDown = false, CurrentVersion = "0.2.2", Install = (m, _) => installed.Add(m) },
        };

        await worker.HeartbeatAsync(force: true, CancellationToken.None);

        Assert.Contains("/downloads/" + Name, server.Paths);
        Assert.Single(installed);
    }

    [Fact]
    public void AfterAStartTheHelperHasTwoMinutesBeforeItsAbsenceIsReported()
    {
        var clock = new FakeClock();
        var state = new ServiceState(new MemoryStore(), clock);
        Assert.Null(state.Reported());                       // just started: hold the heartbeat
        clock.Advance(ServiceState.HelperGrace);
        Assert.Equal(ProtectionState.Off, state.Reported());  // nobody came: off, and loud

        var fresh = new ServiceState(new MemoryStore(), clock);
        fresh.HelperSeenAt = fresh.HelperStateAt = clock.Now;
        fresh.Helper = new ProtectionState(true, "granted", true);
        Assert.Equal(fresh.Helper, fresh.Reported());
    }

    [Fact]
    public void AnOutdatedHelperIsRestartedOncePerVersionAndHeldForAMinute()
    {
        var clock = new FakeClock();
        var state = new ServiceState(new MemoryStore(), clock);
        var restarted = new List<int>();
        state.HelperRestartRequested += restarted.Add;
        state.HelperSeenAt = state.HelperStateAt = clock.Now;
        state.Helper = new ProtectionState(true, "granted", true);

        Assert.False(state.RestartOutdatedHelper("0.3.0", 42, "0.3.0"));
        Assert.True(state.RestartOutdatedHelper("0.2.2", 42, "0.3.0"));
        Assert.False(state.RestartOutdatedHelper("0.2.2", 43, "0.3.0"));   // never a restart loop
        Assert.Equal([42], restarted);

        state.Helper = ProtectionState.Off;                  // the old helper disconnected
        Assert.Null(state.Reported());
        clock.Advance(TimeSpan.FromSeconds(61));
        Assert.Equal(ProtectionState.Off, state.Reported()); // the new one never came
    }
}
