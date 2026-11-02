using System.Text.Json;
using System.Text.Json.Serialization;

namespace Watchman.Core;

/// <summary>
/// One timing histogram: count, sum, max and counts per bucket (ADR-025). Buckets hold samples at or
/// below each bound in <see cref="Metrics.BoundsMs"/>; the last holds everything slower. Histograms
/// merge by adding, so the server can sum a day of heartbeats.
/// </summary>
public sealed class Timing
{
    [JsonPropertyName("n")] public long N { get; set; }
    [JsonPropertyName("sum")] public double Sum { get; set; }
    [JsonPropertyName("max")] public double Max { get; set; }
    [JsonPropertyName("buckets")] public long[] Buckets { get; set; } = new long[Metrics.BucketCount];

    public void Add(double ms)
    {
        N++;
        Sum += ms;
        if (ms > Max) Max = ms;
        Buckets[Metrics.BucketOf(ms)]++;
    }

    public void Merge(Timing other)
    {
        N += other.N;
        Sum += other.Sum;
        Max = Math.Max(Max, other.Max);
        for (var i = 0; i < Buckets.Length && i < other.Buckets.Length; i++) Buckets[i] += other.Buckets[i];
    }

    /// <summary>The bucket bound at or below which a share q of the samples fall, capped at the max.</summary>
    public double Percentile(double q)
    {
        if (N == 0) return 0;
        var want = q * N;
        long seen = 0;
        for (var i = 0; i < Buckets.Length; i++)
        {
            seen += Buckets[i];
            if (seen >= want) return Math.Min(i < Metrics.BoundsMs.Length ? Metrics.BoundsMs[i] : Max, Max);
        }
        return Max;
    }
}

/// <summary>What a window of work cost: counters and timing histograms. The heartbeat's `metrics` field.</summary>
public sealed class MetricsSnapshot
{
    [JsonPropertyName("window_s")] public long WindowS { get; set; }
    [JsonPropertyName("counters")] public Dictionary<string, long> Counters { get; set; } = new();
    [JsonPropertyName("timings")] public Dictionary<string, Timing> Timings { get; set; } = new();

    public bool IsEmpty => Counters.Count == 0 && Timings.Count == 0;

    public void Merge(MetricsSnapshot other)
    {
        WindowS += other.WindowS;
        foreach (var (k, v) in other.Counters) Counters[k] = Counters.GetValueOrDefault(k) + v;
        foreach (var (k, t) in other.Timings)
        {
            if (!Timings.TryGetValue(k, out var have)) Timings[k] = have = new Timing();
            have.Merge(t);
        }
    }
}

/// <summary>Thread-safe tallies for one window; <see cref="Take"/> hands the window over and starts a new one.</summary>
public sealed class Metrics(IClock clock)
{
    public static readonly double[] BoundsMs = { 1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024 };
    public const int BucketCount = 12;
    private readonly object _lock = new();
    private MetricsSnapshot _window = new();
    private DateTimeOffset _since = clock.Now;

    public static int BucketOf(double ms)
    {
        for (var i = 0; i < BoundsMs.Length; i++) if (ms <= BoundsMs[i]) return i;
        return BoundsMs.Length;
    }

    public void Count(string name, long n = 1)
    {
        lock (_lock) _window.Counters[name] = _window.Counters.GetValueOrDefault(name) + n;
    }

    public void Time(string name, double ms)
    {
        lock (_lock)
        {
            if (!_window.Timings.TryGetValue(name, out var t)) _window.Timings[name] = t = new Timing();
            t.Add(ms);
        }
    }

    public MetricsSnapshot Take()
    {
        lock (_lock)
        {
            var w = _window;
            w.WindowS = (long)Math.Round((clock.Now - _since).TotalSeconds);
            _window = new MetricsSnapshot();
            _since = clock.Now;
            return w;
        }
    }
}

/// <summary>Append-only JSON lines with one rotation: at <c>maxBytes</c> the file becomes <c>.1</c>.</summary>
public sealed class MetricsLog(string path, long maxBytes = 5 * 1024 * 1024)
{
    private readonly object _lock = new();
    public string Path { get; } = path;

    public void Append(object record)
    {
        var line = JsonSerializer.Serialize(record) + "\n";
        lock (_lock)
        {
            try
            {
                Directory.CreateDirectory(System.IO.Path.GetDirectoryName(Path)!);
                if (File.Exists(Path) && new FileInfo(Path).Length + line.Length > maxBytes)
                    File.Move(Path, Path + ".1", overwrite: true);
                File.AppendAllText(Path, line);
            }
            catch (IOException)
            {
                // A log that cannot be written must never stop protection.
            }
        }
    }
}
