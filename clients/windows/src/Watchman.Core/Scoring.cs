using System.Drawing;

namespace Watchman.Core;

/// <summary>A detector that returns boxes for a frame. ONNX Runtime behind it, or a fake.</summary>
public interface IDetector : IDisposable
{
    ModelEntry Entry { get; }
    List<Box> Detect(Bitmap frame);
}

public sealed class FakeDetector(ModelEntry entry) : IDetector
{
    public ModelEntry Entry { get; } = entry;
    public Func<Bitmap, List<Box>> Answer { get; set; } = _ => new List<Box>();
    public int Calls { get; private set; }
    public List<Box> Detect(Bitmap frame) { Calls++; return Answer(frame); }
    public void Dispose() { }
}

public sealed class OnnxDetector : IDetector
{
    private readonly Microsoft.ML.OnnxRuntime.InferenceSession _session;
    private readonly string _inputName;
    public ModelEntry Entry { get; }
    public string Provider { get; }

    public OnnxDetector(ModelEntry entry, string modelPath, bool tryDirectML = true, int cpuThreads = 2)
    {
        Entry = entry;
        var options = new Microsoft.ML.OnnxRuntime.SessionOptions();
        Provider = "cpu";
        if (tryDirectML)
        {
            try
            {
                options.AppendExecutionProvider_DML(0);
                Provider = "directml";
            }
            catch (Exception)
            {
                options = new Microsoft.ML.OnnxRuntime.SessionOptions();
            }
        }
        if (Provider == "cpu") options.IntraOpNumThreads = cpuThreads;
        _session = new Microsoft.ML.OnnxRuntime.InferenceSession(modelPath, options);
        _inputName = _session.InputMetadata.Keys.First();
    }

    public List<Box> Detect(Bitmap frame)
    {
        var (tensor, lb) = Vision.LetterboxTensor(frame, Entry.Input);
        var size = Entry.Input.Size;
        var input = new Microsoft.ML.OnnxRuntime.Tensors.DenseTensor<float>(tensor, new[] { 1, 3, size, size });
        using var results = _session.Run(new[] { Microsoft.ML.OnnxRuntime.NamedOnnxValue.CreateFromTensor(_inputName, input) });
        var first = results.First().AsTensor<float>();
        return Vision.DecodeYolo(first.ToArray(), first.Dimensions.ToArray(), Entry.Output, lb);
    }

    public void Dispose() => _session.Dispose();
}

/// <summary>What a pass over one frame found, ready for the overlay and the event queue.</summary>
public sealed record Finding(Rectangle Region, double Score, List<string> Labels, string Hash, byte[] Jpeg);

/// <summary>One detector inference: the whole frame, or one native-resolution tile of it.</summary>
public sealed record Pass(Rectangle Region, bool WholeFrame);

/// <summary>
/// Runs the detector over a frame as a sweep of passes (the whole frame, then tiles), so a caller
/// can spread the sweep across the interval instead of firing every inference in one burst.
/// </summary>
public sealed class FramePipeline(IDetector detector, Func<Thresholds> thresholds, Func<IReadOnlyCollection<string>?>? explicitLabels = null)
{
    public int TileSize => detector.Entry.Input.Size;

    /// <summary>The sweep for a frame of this size: the whole frame first, then each tile.</summary>
    public List<Pass> Passes(int width, int height)
    {
        var passes = new List<Pass> { new(new Rectangle(0, 0, width, height), true) };
        foreach (var tile in Vision.Tiles(width, height, TileSize))
        {
            if (tile.Width >= width && tile.Height >= height) continue; // the whole-frame pass did it
            passes.Add(new Pass(tile, false));
        }
        return passes;
    }

    /// <summary>One inference; findings are in frame coordinates.</summary>
    public List<Finding> ExaminePass(Bitmap frame, Pass pass)
    {
        return ToFindings(frame, Boxes(frame, pass));
    }

    /// <summary>The whole sweep at once. For the self-test and the tests; the helper spreads it.</summary>
    public List<Finding> Examine(Bitmap frame)
    {
        var all = new List<Box>();
        foreach (var pass in Passes(frame.Width, frame.Height)) all.AddRange(Boxes(frame, pass));
        return ToFindings(frame, all);
    }

    private List<Box> Boxes(Bitmap frame, Pass pass)
    {
        if (pass.WholeFrame) return detector.Detect(frame);
        using var crop = Vision.Crop(frame, pass.Region);
        return detector.Detect(crop).Select(b => b with { X = b.X + pass.Region.X, Y = b.Y + pass.Region.Y }).ToList();
    }

    private List<Finding> ToFindings(Bitmap frame, List<Box> boxes)
    {
        var t = thresholds();
        // The policy's label set (the coverage setting) wins over the manifest's default.
        var labels = new HashSet<string>(explicitLabels?.Invoke() ?? detector.Entry.Output.ExplicitLabels ?? new List<string>());
        var hits = boxes.Where(b => labels.Contains(b.Label) && b.Score >= t.DetectorCover).ToList();
        var findings = new List<Finding>();
        foreach (var box in Merge(hits))
        {
            var region = Pad(box.Rect, frame);
            using var crop = Vision.Crop(frame, region);
            var hash = DHash.Compute(crop);
            findings.Add(new Finding(region, box.Score, box.Labels, hash, Vision.ToJpeg(crop)));
        }
        return findings;
    }

    private static Rectangle Pad(Rectangle r, Bitmap frame)
    {
        var pad = Math.Max(8, (int)(Math.Max(r.Width, r.Height) * 0.15));
        var padded = Rectangle.Inflate(r, pad, pad);
        return Rectangle.Intersect(padded, new Rectangle(0, 0, frame.Width, frame.Height));
    }

    public sealed record MergedBox(Rectangle Rect, double Score, List<string> Labels);

    /// <summary>Union overlapping boxes so one patch covers one picture.</summary>
    public static List<MergedBox> Merge(List<Box> boxes)
    {
        var groups = new List<MergedBox>();
        foreach (var b in boxes.OrderByDescending(b => b.Score))
        {
            var r = b.ToRectangle();
            var idx = groups.FindIndex(g => g.Rect.IntersectsWith(r));
            if (idx < 0) { groups.Add(new MergedBox(r, b.Score, new List<string> { b.Label })); continue; }
            var g = groups[idx];
            var labels = g.Labels.Contains(b.Label) ? g.Labels : g.Labels.Append(b.Label).ToList();
            groups[idx] = new MergedBox(Rectangle.Union(g.Rect, r), Math.Max(g.Score, b.Score), labels);
        }
        return groups;
    }
}

/// <summary>
/// Spreads a sweep of N passes evenly over the interval: one inference per tick, never a burst.
/// In game mode (a full-screen app in front) there is one whole-frame pass per interval instead.
/// </summary>
public sealed class SweepScheduler
{
    public int IntervalMs { get; private set; }
    public int PassCount { get; private set; }
    public bool GameMode { get; private set; }
    private int _next;

    public bool OnBattery { get; private set; }

    public SweepScheduler(int intervalMs, int passCount, bool gameMode = false, bool onBattery = false) =>
        Configure(intervalMs, passCount, gameMode, onBattery);

    /// <summary>On battery the interval doubles (ADR-023): half the passes per hour for the same coverage of what changed.</summary>
    public void Configure(int intervalMs, int passCount, bool gameMode, bool onBattery = false)
    {
        OnBattery = onBattery;
        IntervalMs = Math.Max(1000, intervalMs) * (onBattery ? 2 : 1);
        PassCount = Math.Max(1, passCount);
        GameMode = gameMode;
        if (_next >= EffectiveCount) _next = 0;
    }

    public int EffectiveCount => GameMode ? 1 : PassCount;

    /// <summary>Milliseconds between ticks so the whole sweep fits the interval.</summary>
    public int TickMs => Math.Max(100, IntervalMs / EffectiveCount);

    /// <summary>The pass index to run now; index 0 is the whole frame and starts a new sweep.</summary>
    public int Next()
    {
        var i = _next;
        _next = (_next + 1) % EffectiveCount;
        return i;
    }

    public bool SweepStarts => _next == 0;
}

/// <summary>
/// One candidate pass of a sweep: where it is, its fingerprint now, whether it is a whole frame, and,
/// when the page scrolled, the fingerprint of the content that was at the place it scrolled from.
/// </summary>
public sealed record Candidate(string Key, string Fingerprint, bool WholeFrame, string? Previous = null);

/// <summary>
/// Decides which passes of a sweep run (ADR-023). A pass whose region's fingerprint is within
/// <see cref="UnchangedBits"/> of the last sweep's is skipped; the caller keeps its last findings on
/// the overlay. When more than <see cref="MotionShare"/> of the tiles changed at once (video, a game,
/// a fast scroll) only the whole-frame passes run: motion mode. A still desktop runs nothing.
/// </summary>
public sealed class SweepPlanner
{
    public const int UnchangedBits = 2;
    public const double MotionShare = 0.5;
    private readonly Dictionary<string, string> _last = new();

    public bool Motion { get; private set; }
    public int Ran { get; private set; }
    public int Skipped { get; private set; }
    /// <summary>Indexes judged unchanged this sweep (same content, or the same content scrolled into place).</summary>
    public HashSet<int> Unchanged { get; } = new();

    /// <summary>Indexes into <paramref name="candidates"/> to run this sweep.</summary>
    public List<int> Plan(IReadOnlyList<Candidate> candidates)
    {
        var changed = new bool[candidates.Count];
        var known = new bool[candidates.Count];
        for (var i = 0; i < candidates.Count; i++)
        {
            var c = candidates[i];
            known[i] = _last.TryGetValue(c.Key, out var prev);
            // After a scroll, compare with what used to be where this content came from.
            var against = c.Previous ?? prev;
            changed[i] = !known[i] || against is null || DHash.Hamming(against, c.Fingerprint) > UnchangedBits;
            _last[c.Key] = c.Fingerprint;
        }
        Unchanged.Clear();
        for (var i = 0; i < candidates.Count; i++) if (!changed[i]) Unchanged.Add(i);
        // Motion is judged on tiles seen before; a first sweep (or a new monitor) is new, not moving.
        var tiles = Enumerable.Range(0, candidates.Count).Where(i => !candidates[i].WholeFrame && known[i]).ToList();
        var changedTiles = tiles.Count(i => changed[i]);
        Motion = tiles.Count > 0 && changedTiles > tiles.Count * MotionShare;
        var run = Enumerable.Range(0, candidates.Count)
            .Where(i => Motion ? candidates[i].WholeFrame : changed[i])
            .ToList();
        Ran = run.Count;
        Skipped = candidates.Count - run.Count;
        // Keys that vanished (a monitor unplugged) are forgotten so they count as new when they return.
        foreach (var k in _last.Keys.Where(k => candidates.All(c => c.Key != k)).ToList()) _last.Remove(k);
        return run;
    }
}
