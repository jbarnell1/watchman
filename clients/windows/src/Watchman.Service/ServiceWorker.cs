using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Watchman.Core;

namespace Watchman.Service;

/// <summary>Server traffic: heartbeats every five minutes (at once on a state change), the event
/// queue every 30 seconds, policy when its version changes, the model when the manifest changes.</summary>
public sealed class ServiceWorker(ServiceState state, ILogger<ServiceWorker> log) : BackgroundService
{
    private static readonly TimeSpan HeartbeatEvery = TimeSpan.FromMinutes(5);
    private static readonly TimeSpan FlushEvery = TimeSpan.FromSeconds(30);
    private static readonly TimeSpan HelperGrace = TimeSpan.FromMinutes(2);
    private readonly SemaphoreSlim _gate = new(1, 1);
    private DateTimeOffset _lastHeartbeat = DateTimeOffset.MinValue;
    private ProtectionState _lastReported = ProtectionState.Off;
    private readonly DateTimeOffset _booted = DateTimeOffset.UtcNow;

    protected override async Task ExecuteAsync(CancellationToken ct)
    {
        state.Changed += () => _ = HeartbeatAsync(force: false, ct);
        while (!ct.IsCancellationRequested)
        {
            try
            {
                await FlushAsync(ct);
                await CheckPolicyAsync();
                var reported = CurrentState();
                var due = state.Clock.Now - _lastHeartbeat >= HeartbeatEvery;
                if (due || reported != _lastReported) await HeartbeatAsync(force: true, ct);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                log.LogWarning(ex, "worker loop");
            }
            await Task.Delay(FlushEvery, ct);
        }
    }

    /// <summary>Settings changed on the portal reach this PC within a loop (ADR-031).</summary>
    private async Task CheckPolicyAsync()
    {
        using var api = state.Api();
        if (api is null) return;
        try
        {
            var v = await api.PolicyVersionAsync();
            var p = state.Policy;
            if (p is null || p.Version != v.PolicyVersion || p.PausedUntil != v.PausedUntil) state.Policy = await api.PolicyAsync();
        }
        catch (ApiException ex) when (ex.Status == 401)
        {
            Reject();
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            log.LogDebug(ex, "policy check");
        }
    }

    /// <summary>What the service tells the server. No helper for two minutes means capture is off.</summary>
    private ProtectionState CurrentState()
    {
        var seen = state.HelperSeenAt;
        if (seen is null || state.Clock.Now - seen > HelperGrace) return new ProtectionState(false, "denied", false);
        return state.Helper;
    }

    public async Task HeartbeatAsync(bool force, CancellationToken ct)
    {
        using var api = state.Api();
        if (api is null) return;
        if (!await _gate.WaitAsync(0, ct)) return;
        try
        {
            var reported = CurrentState();
            if (!force && reported == _lastReported && state.Clock.Now - _lastHeartbeat < HeartbeatEvery) return;
            var work = state.TakeMetrics();
            HeartbeatResponse r;
            try
            {
            r = await api.HeartbeatAsync(new
            {
                sent_at = Iso.Format(state.Clock.Now), app_version = "0.1.0", booted_at = Iso.Format(_booted),
                safe_mode = IsSafeMode(), battery_pct = (int?)null, queued_events = state.Queue.Count,
                state = new
                {
                    protection = reported.Protection, accessibility = (bool?)null, capture = reported.Capture,
                    overlay = reported.Overlay, device_admin = (bool?)null, extension = (bool?)null, vpn = (bool?)null,
                },
                metrics = work.IsEmpty ? null : work,
            });
            }
            catch
            {
                state.AddMetrics(work); // not delivered: keep it for the next heartbeat
                throw;
            }
            _lastHeartbeat = state.Clock.Now;
            _lastReported = reported;
            state.Rejected = false;
            var policy = state.Policy;
            if (policy is null || policy.Version != r.PolicyVersion || policy.PausedUntil != r.PausedUntil)
            {
                state.Policy = await api.PolicyAsync();
                log.LogInformation("policy {Version}", state.Policy?.Version);
            }
            if (state.DetectorPath is null || state.SmallDetectorPath is null)
                await state.EnsureDetectorAsync(api, m => log.LogInformation("{Message}", m));
        }
        catch (ApiException ex) when (ex.Status == 401)
        {
            Reject();
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            log.LogWarning(ex, "heartbeat");
        }
        finally
        {
            _gate.Release();
        }
    }

    /// <summary>A 401 never unpairs (fail closed): pairing, policy, queue and protection stay, the
    /// heartbeats go on, and the status says to pair again with a new code.</summary>
    private void Reject()
    {
        if (!state.Rejected) log.LogWarning("the server answered 401; protection stays on until this PC pairs again");
        state.Rejected = true;
    }

    private async Task FlushAsync(CancellationToken ct)
    {
        using var api = state.Api();
        if (api is null) return;
        List<string> wanted;
        try { wanted = await state.Queue.FlushAsync(api.EventsAsync); }
        catch (ApiException ex) when (ex.Status == 401) { Reject(); return; }
        foreach (var id in wanted)
        {
            if (!state.PendingImages.TryRemove(id, out var jpeg)) continue;
            try { await api.UploadImageAsync(id, jpeg); }
            catch (Exception ex) { log.LogWarning(ex, "upload {Id}", id); }
        }
    }

    private static bool IsSafeMode()
    {
        try { return Environment.GetEnvironmentVariable("SAFEBOOT_OPTION") is not null || GetSystemMetrics(67) != 0; }
        catch { return false; }
    }

    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern int GetSystemMetrics(int index); // SM_CLEANBOOT = 67
}
