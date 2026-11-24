using System.Collections.Concurrent;
using System.Security.Cryptography;
using Watchman.Core;

namespace Watchman.Service;

/// <summary>Pairing, policy, the model on disk, the queue and the helper's reported state. Shared by
/// the worker (server traffic) and the pipe server (helper traffic).</summary>
public sealed class ServiceState(IStore store, IClock clock)
{
    public IClock Clock { get; } = clock;
    public EventQueue Queue { get; } = new(store);
    public ConcurrentDictionary<string, byte[]> PendingImages { get; } = new();
    public ProtectionState Helper { get; set; } = ProtectionState.Off;
    public DateTimeOffset? HelperSeenAt { get; set; }
    /// <summary>When the helper last said what it is doing (a state message), as opposed to any message.</summary>
    public DateTimeOffset? HelperStateAt { get; set; }

    /// <summary>No helper for two minutes means capture is off.</summary>
    public static readonly TimeSpan HelperGrace = TimeSpan.FromMinutes(2);
    private static readonly TimeSpan RestartGrace = TimeSpan.FromSeconds(60);
    private DateTimeOffset _holdUntil = clock.Now + HelperGrace;
    private readonly HashSet<string> _restartedFor = new();

    /// <summary>What heartbeats report, or null to hold the heartbeat. After the service starts (an
    /// update, a reboot) and while an outdated helper is replaced, the helper has a moment to say what
    /// it is doing before its absence counts as off; after that a missing helper is reported (ADR-061).</summary>
    public ProtectionState? Reported()
    {
        var now = Clock.Now;
        if (HelperStateAt is null && now < _holdUntil) return null;
        var seen = HelperSeenAt;
        if (seen is null || now - seen > HelperGrace) return ProtectionState.Off;
        return Helper;
    }

    /// <summary>The installer replaces the helper's files but not the helper that is running, so a
    /// helper of another version is restarted, once per version (ADR-061).</summary>
    public event Action<int>? HelperRestartRequested;

    public bool RestartOutdatedHelper(string helperVersion, int pid, string serviceVersion)
    {
        if (helperVersion == serviceVersion || pid <= 0) return false;
        lock (_restartedFor) if (!_restartedFor.Add(helperVersion)) return false;
        HelperStateAt = null;
        _holdUntil = Clock.Now + RestartGrace;
        HelperRestartRequested?.Invoke(pid);
        return true;
    }

    /// <summary>The last update this PC tried, so a failing install is retried hours later, not every heartbeat.</summary>
    public UpdateAttempt? LastUpdateAttempt
    {
        get => store.ReadJson<UpdateAttempt>("update.json");
        set { if (value is null) store.Delete("update.json"); else store.WriteJson("update.json", value); }
    }
    public event Action? Changed;
    private readonly object _metricsLock = new();
    private MetricsSnapshot _metrics = new();

    /// <summary>The helper's work since the last heartbeat (ADR-025).</summary>
    public void AddMetrics(MetricsSnapshot s) { lock (_metricsLock) _metrics.Merge(s); }

    public MetricsSnapshot TakeMetrics()
    {
        lock (_metricsLock) { var m = _metrics; _metrics = new MetricsSnapshot(); return m; }
    }

    /// <summary>Pairing again with a new code replaces the token and clears <see cref="Rejected"/>.</summary>
    public Pairing? Pairing
    {
        get => store.ReadJson<Pairing>("pairing.json");
        set
        {
            if (value is null) store.Delete("pairing.json"); else store.WriteJson("pairing.json", value);
            store.Delete("rejected.txt");
            Changed?.Invoke();
        }
    }

    public const string RejectedMessage = "The server no longer accepts this device. Protection stays on. Pair again with a new code.";

    /// <summary>The server answered 401. The token, the policy and protection stay exactly as they
    /// were and heartbeats go on: a fake or hostile server must not be able to switch protection off.</summary>
    public bool Rejected
    {
        get => store.ReadText("rejected.txt") is not null;
        set
        {
            if (value == Rejected) return;
            if (value) store.WriteText("rejected.txt", Iso.Format(Clock.Now)); else store.Delete("rejected.txt");
        }
    }

    /// <summary>How the worker reaches the server; tests swap in an Api over a fake handler.</summary>
    public Func<Pairing, Api> ApiFactory { get; set; } = p => new Api(p.Server, p.Token);

    public Policy? Policy
    {
        get => store.ReadJson<Policy>("policy.json");
        set { if (value is null) store.Delete("policy.json"); else store.WriteJson("policy.json", value); Changed?.Invoke(); }
    }

    public ModelEntry? Detector
    {
        get => store.ReadJson<ModelEntry>("detector.json");
        set { if (value is null) store.Delete("detector.json"); else store.WriteJson("detector.json", value); Changed?.Invoke(); }
    }

    public ModelEntry? SmallDetector
    {
        get => store.ReadJson<ModelEntry>("detector-small.json");
        set { if (value is null) store.Delete("detector-small.json"); else store.WriteJson("detector-small.json", value); Changed?.Invoke(); }
    }

    public string? DetectorPath => PathOf(Detector);
    public string? SmallDetectorPath => PathOf(SmallDetector);

    private static string? PathOf(ModelEntry? d)
    {
        if (d is null) return null;
        var path = Path.Combine(Paths.ModelsDir, d.File);
        return File.Exists(path) ? path : null;
    }

    public Api? Api()
    {
        var p = Pairing;
        return p is null ? null : ApiFactory(p);
    }

    public ConfigMessage Config()
    {
        // No policy fetched yet means the built-in defaults, never no protection.
        var p = Policy ?? Core.Policy.Defaults;
        return new ConfigMessage(Pairing is not null, Pairing?.ServerName, p, DetectorPath, Detector, p.PausedUntil,
            SmallDetectorPath, SmallDetector);
    }

    public void NotifyChanged() => Changed?.Invoke();

    private string? _lastConfig;

    /// <summary>The pair command runs in its own process and only writes files. The service calls this
    /// every loop so a new pairing, model or policy reaches the helper without a restart.</summary>
    public bool ConfigChangedOnDisk()
    {
        var now = Wire.Encode(Config());
        var changed = _lastConfig is not null && now != _lastConfig;
        _lastConfig = now;
        return changed;
    }

    /// <summary>Download the detectors named in the server's manifest unless the cached copies match
    /// their hashes: the large one for the GPU sweep, the small one for game mode on the CPU.</summary>
    public async Task EnsureDetectorAsync(Api api, Action<string> log)
    {
        var manifest = await api.ManifestAsync();
        var large = manifest.Models.FirstOrDefault(m => m.Role == "detector_large");
        var small = manifest.Models.FirstOrDefault(m => m.Role == "detector_small");
        if (large is null && small is null) { log("the server's manifest has no detector"); return; }
        Directory.CreateDirectory(Paths.ModelsDir);
        foreach (var entry in new[] { large ?? small!, small ?? large! }.Distinct())
        {
            var path = Path.Combine(Paths.ModelsDir, entry.File);
            if (!(File.Exists(path) && Sha256(path) == entry.Sha256))
            {
                log($"downloading {entry.File} ({entry.Bytes} bytes)");
                var bytes = await api.BytesAsync(entry.Url);
                var digest = Convert.ToHexStringLower(SHA256.HashData(bytes));
                if (digest != entry.Sha256) throw new InvalidOperationException($"{entry.File} hash mismatch");
                await File.WriteAllBytesAsync(path + ".tmp", bytes);
                File.Move(path + ".tmp", path, overwrite: true);
            }
        }
        Detector = large ?? small;
        SmallDetector = small ?? large;
    }

    private static string Sha256(string path)
    {
        using var f = File.OpenRead(path);
        return Convert.ToHexStringLower(SHA256.HashData(f));
    }
}
