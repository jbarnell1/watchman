using System.IO.Pipes;
using System.Security.AccessControl;
using System.Security.Principal;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Watchman.Core;

namespace Watchman.Service;

/// <summary>The helper's side of the service: one named pipe, one helper at a time, JSON lines.</summary>
public sealed class PipeServer(ServiceState state, ILogger<PipeServer> log) : BackgroundService
{
    private StreamWriter? _writer;
    private readonly SemaphoreSlim _writeGate = new(1, 1);

    protected override async Task ExecuteAsync(CancellationToken ct)
    {
        state.Changed += () => _ = SendConfigAsync();
        while (!ct.IsCancellationRequested)
        {
            try
            {
                using var pipe = Create();
                await pipe.WaitForConnectionAsync(ct);
                log.LogInformation("helper connected");
                using var reader = new StreamReader(pipe);
                _writer = new StreamWriter(pipe) { AutoFlush = true };
                await SendConfigAsync();
                while (!ct.IsCancellationRequested && pipe.IsConnected)
                {
                    var line = await reader.ReadLineAsync(ct);
                    if (line is null) break;
                    await HandleAsync(line);
                }
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                log.LogWarning(ex, "pipe");
                await Task.Delay(1000, ct);
            }
            finally
            {
                _writer = null;
                state.Helper = ProtectionState.Off;
                state.NotifyChanged();
                log.LogInformation("helper disconnected");
            }
        }
    }

    private static NamedPipeServerStream Create()
    {
        var security = new PipeSecurity();
        security.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.AuthenticatedUserSid, null),
            PipeAccessRights.ReadWrite, AccessControlType.Allow));
        security.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null),
            PipeAccessRights.FullControl, AccessControlType.Allow));
        return NamedPipeServerStreamAcl.Create(Pipe.Name, PipeDirection.InOut, 1, PipeTransmissionMode.Byte,
            PipeOptions.Asynchronous, 1 << 16, 1 << 20, security);
    }

    private async Task SendAsync(ServiceMessage msg)
    {
        var w = _writer;
        if (w is null) return;
        await _writeGate.WaitAsync();
        try { await w.WriteLineAsync(Wire.Encode(msg)); }
        catch (Exception ex) { log.LogWarning(ex, "pipe write"); }
        finally { _writeGate.Release(); }
    }

    public Task SendConfigAsync() => SendAsync(state.Config());

    private async Task HandleAsync(string line)
    {
        HelperMessage? msg;
        try { msg = Wire.Decode<HelperMessage>(line); }
        catch (Exception ex) { log.LogWarning(ex, "bad pipe message"); return; }
        state.HelperSeenAt = state.Clock.Now;
        switch (msg)
        {
            case HelloMessage hello:
                log.LogInformation("helper {Version} for {User}", hello.Version, hello.User);
                break;
            case StateMessage s:
                var next = new ProtectionState(s.Capture && s.Overlay, s.Capture ? "granted" : "denied", s.Overlay);
                if (next != state.Helper) { state.Helper = next; state.NotifyChanged(); }
                break;
            case FindingMessage f:
                Record(f);
                break;
            case StatsMessage st:
                state.Queue.Push(new WatchmanEvent
                {
                    Type = "daily_stats", Action = "none", OccurredAt = Iso.Format(state.Clock.Now), App = "windows",
                    Detail = new() { ["screens_examined"] = st.ScreensExamined, ["images_scored"] = st.ImagesScored, ["minutes_protected"] = st.MinutesProtected },
                });
                break;
            case MetricsMessage m:
                state.AddMetrics(m.Snapshot);
                break;
            case UnlockMessage u:
                await ReplyAsync("unlock", async api =>
                {
                    var until = await api.UnlockAsync(u.Code);
                    var p = state.Policy;
                    if (p is not null) state.Policy = p with { PausedUntil = until };
                    return $"paused until {until}";
                });
                break;
            case RequestUnlockMessage r:
                await ReplyAsync("request_unlock", async api => { await api.RequestUnlockAsync(r.Minutes, r.Reason); return "asked"; });
                break;
        }
    }

    private void Record(FindingMessage f)
    {
        var ev = new WatchmanEvent
        {
            Type = f.Blocked ? "screen_blocked" : f.Covered ? "image_covered" : "image_flagged",
            Action = f.Blocked ? "blocked" : f.Covered ? "covered" : "flagged",
            OccurredAt = Iso.Format(state.Clock.Now), App = f.App, Title = f.Title, Hash = f.Hash, HasImage = f.JpegBase64.Length > 0,
            Scores = new() { ["classifier"] = Math.Round(f.Score, 3), ["detector"] = Math.Round(f.Score, 3), ["labels"] = f.Labels },
            Region = new() { ["w"] = f.W, ["h"] = f.H, ["screen_w"] = f.ScreenW, ["screen_h"] = f.ScreenH },
            Detail = new() { ["x"] = f.X, ["y"] = f.Y },
        };
        if (f.JpegBase64.Length > 0) state.PendingImages[ev.Id] = Convert.FromBase64String(f.JpegBase64);
        state.Queue.Push(ev);
    }

    private async Task ReplyAsync(string what, Func<Api, Task<string>> action)
    {
        using var api = state.Api();
        if (api is null) { await SendAsync(new ResultMessage(what, false, "not paired")); return; }
        try { await SendAsync(new ResultMessage(what, true, await action(api))); }
        catch (ApiException ex) { await SendAsync(new ResultMessage(what, false, ex.Message)); }
        catch (Exception ex) { await SendAsync(new ResultMessage(what, false, ex.Message)); }
    }
}
