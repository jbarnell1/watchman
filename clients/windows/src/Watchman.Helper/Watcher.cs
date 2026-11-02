using System.Diagnostics;
using System.Drawing;
using Microsoft.Win32;
using Watchman.Core;

namespace Watchman.Helper;

/// <summary>
/// The loop. A sweep is one whole-frame pass plus one pass per native tile for every monitor; the
/// scheduler spreads it over the whole-screen interval so each tick runs exactly one inference
/// (about 17 ms on DirectML), never a burst. When a full-screen app that is not a browser is in
/// front (a game), the sweep collapses to one whole-frame pass per interval with the small detector
/// on the CPU at below-normal priority, so the GPU is left alone. Nothing while the session is locked.
/// </summary>
public sealed class Watcher : IDisposable
{
    private readonly IScreenCapture _capture;
    private readonly Overlays _overlays;
    private readonly PipeClient _pipe;
    private readonly System.Windows.Forms.Timer _timer = new();
    private readonly Dictionary<string, DateTime> _recentHashes = new();
    private readonly SweepScheduler _scheduler = new(5000, 1);
    private FramePipeline? _gpuPipeline;
    private FramePipeline? _cpuPipeline;
    private IDetector? _gpuDetector;
    private IDetector? _cpuDetector;
    private ConfigMessage? _config;
    private bool _locked;
    private bool _busy;
    private int _screensExamined;
    private int _imagesScored;
    private DateTime _statsSince = DateTime.UtcNow;
    private DateTime _lastStats = DateTime.UtcNow;
    private readonly List<ScreenFrame> _sweepFrames = new();
    private readonly List<(ScreenFrame Frame, Pass Pass, string Key)> _sweep = new();
    private readonly SweepPlanner _planner = new();
    // Per screen: the thumbnail of the last sweep, and what was found on it (frame coordinates).
    private readonly Dictionary<int, GrayThumb> _thumbs = new();
    private readonly Dictionary<int, List<Finding>> _kept = new();
    // ADR-025: a minute of work goes to the service (and the heartbeat) and to a local log.
    private readonly Metrics _metrics = new(new SystemClock());
    private readonly MetricsLog _log = new(Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Watchman", "metrics.jsonl"));
    private DateTime _lastFlush = DateTime.UtcNow;

    public string Status { get; private set; } = "Starting";
    /// <summary>What the last sweep did: passes run and skipped, motion, battery. For the tray tooltip.</summary>
    public string Detail { get; private set; } = "";
    public string? Provider { get; private set; }
    public bool GameMode => _scheduler.GameMode;
    public event Action? StatusChanged;

    public Watcher(IScreenCapture capture, Overlays overlays, PipeClient pipe)
    {
        _capture = capture;
        _overlays = overlays;
        _pipe = pipe;
        _pipe.ConfigReceived += OnConfig;
        _pipe.Connected += on => { if (!on) SetStatus("Waiting for the Watchman service"); };
        SystemEvents.SessionSwitch += (_, e) =>
        {
            _locked = e.Reason is SessionSwitchReason.SessionLock or SessionSwitchReason.ConsoleDisconnect;
        };
        _timer.Tick += async (_, _) => await TickAsync();
        _timer.Interval = 1000;
        _timer.Start();
    }

    private void SetStatus(string s) { Status = s; StatusChanged?.Invoke(); }

    private void OnConfig(ConfigMessage c)
    {
        _config = c;
        if (!c.Paired) { SetStatus("Not paired. Run WatchmanService.exe pair as an administrator."); return; }
        if (c.DetectorPath is null || c.Detector is null) { SetStatus("Waiting for the model to download"); return; }
        Thresholds Current() => _config?.Policy?.Thresholds ?? new Thresholds(0.8, 0.6, 0.55);
        IReadOnlyCollection<string>? Labels() => _config?.Policy?.ExplicitLabels;
        try
        {
            if (_gpuDetector is null || _gpuDetector.Entry.File != c.Detector.File)
            {
                _gpuDetector?.Dispose();
                var d = new OnnxDetector(c.Detector, c.DetectorPath);
                _gpuDetector = d;
                Provider = d.Provider;
                _gpuPipeline = new FramePipeline(d, Current, Labels);
            }
            var small = c.SmallDetector ?? c.Detector;
            var smallPath = c.SmallDetectorPath ?? c.DetectorPath;
            if (_cpuDetector is null || _cpuDetector.Entry.File != small.File)
            {
                _cpuDetector?.Dispose();
                _cpuDetector = new OnnxDetector(small, smallPath, tryDirectML: false, cpuThreads: 2);
                _cpuPipeline = new FramePipeline(_cpuDetector, Current, Labels);
            }
        }
        catch (Exception ex)
        {
            SetStatus("Model failed to load: " + ex.Message);
            _ = _pipe.SendAsync(new StateMessage(false, false, _capture.Screens.Count));
            return;
        }
        SetStatus(c.PausedUntil is not null && Iso.Parse(c.PausedUntil) > DateTimeOffset.UtcNow
            ? $"Paused until {Iso.Parse(c.PausedUntil).ToLocalTime():HH:mm}"
            : $"Protection on ({Provider})");
        _ = _pipe.SendAsync(new StateMessage(true, true, _capture.Screens.Count));
    }

    private async Task TickAsync()
    {
        await MaybeFlushMetrics();
        if (_busy || _gpuPipeline is null || _cpuPipeline is null || _config?.Policy is null || _locked) return;
        if (_config.PausedUntil is not null && Iso.Parse(_config.PausedUntil) > DateTimeOffset.UtcNow) return;
        _busy = true;
        try
        {
            if (_scheduler.SweepStarts) await StartSweep();
            if (_sweep.Count == 0) return;
            var index = _scheduler.Next();
            if (index >= _sweep.Count) return;
            var (frame, pass, _) = _sweep[index];
            var gameMode = _scheduler.GameMode;
            var pipeline = gameMode ? _cpuPipeline : _gpuPipeline;
            var sw = Stopwatch.StartNew();
            var findings = await RunPass(pipeline, frame.Image, pass, gameMode);
            _metrics.Time(gameMode ? "inference_cpu_ms" : "inference_ms", sw.Elapsed.TotalMilliseconds);
            _metrics.Count("findings", findings.Count);
            _imagesScored++;
            if (!_kept.TryGetValue(frame.Index, out var kept)) _kept[frame.Index] = kept = new List<Finding>();
            kept.AddRange(findings);
            Apply(frame, findings);
            await MaybeStats();
        }
        catch (Exception ex)
        {
            SetStatus("Capture failed: " + ex.Message);
            await _pipe.SendAsync(new StateMessage(false, _overlays.AnyVisible, _capture.Screens.Count));
        }
        finally
        {
            _busy = false;
        }
    }

    /// <summary>
    /// Capture every monitor once and fingerprint every pass region from a one-eighth thumbnail. If
    /// the page scrolled since the last sweep, patches and kept findings move with it and each tile is
    /// compared with the content that scrolled into its place, so only the newly revealed strip runs
    /// (ADR-023). Unchanged passes keep their findings on the overlay. The tick is set so the passes
    /// that do run are spread over the interval.
    /// </summary>
    private async Task StartSweep()
    {
        foreach (var f in _sweepFrames) f.Dispose();
        _sweepFrames.Clear();
        _sweep.Clear();
        var gameMode = Native.IsGameInFront(_capture.Screens);
        var onBattery = SystemInformation.PowerStatus.PowerLineStatus == PowerLineStatus.Offline;
        var pipeline = gameMode ? _cpuPipeline! : _gpuPipeline!;
        var all = new List<(ScreenFrame Frame, Pass Pass, string Key)>();
        var candidates = new List<Candidate>();
        var shifts = new Dictionary<int, int>();
        double captureMs = 0, thumbMs = 0;
        var sw = new Stopwatch();
        for (var i = 0; i < _capture.Screens.Count; i++)
        {
            sw.Restart();
            var frame = await Task.Run(() => _capture.Capture(i));
            captureMs += sw.Elapsed.TotalMilliseconds;
            _metrics.Time("capture_ms", sw.Elapsed.TotalMilliseconds);
            _sweepFrames.Add(frame);

            sw.Restart();
            // Off the UI thread: the overlay paints there.
            _thumbs.TryGetValue(i, out var before);
            var (thumb, shiftPx, scrollMs) = await Task.Run(() =>
            {
                var t = GrayThumb.From(frame.Image);
                if (gameMode || before is null) return (t, 0, -1.0);
                var scrollWatch = Stopwatch.StartNew();
                var (dy, _) = ScrollEstimator.Estimate(before, t, t.Height / 2);
                return (t, dy * GrayThumb.Scale, scrollWatch.Elapsed.TotalMilliseconds);
            });
            if (scrollMs >= 0) _metrics.Time("scroll_estimate_ms", scrollMs);
            if (shiftPx != 0)
            {
                _metrics.Count("scrolls");
                _overlays.For(frame.Bounds).Shift(shiftPx);
                if (_kept.TryGetValue(i, out var moving))
                    _kept[i] = moving.Select(f => ScrollEstimator.Move(f, shiftPx, frame.Image.Size)).OfType<Finding>().ToList();
            }
            shifts[i] = shiftPx;
            foreach (var pass in pipeline.Passes(frame.Image.Width, frame.Image.Height))
            {
                if (gameMode && !pass.WholeFrame) continue;
                var key = $"{i}:{pass.Region.X},{pass.Region.Y},{pass.Region.Width},{pass.Region.Height}";
                var now = thumb.Fingerprint(pass.Region) ?? "0000000000000000";
                var previous = shiftPx != 0 && !pass.WholeFrame && _thumbs.TryGetValue(i, out var b) ? b.Fingerprint(pass.Region, shiftPx) : null;
                all.Add((frame, pass, key));
                candidates.Add(new Candidate(key, now, pass.WholeFrame, previous));
            }
            _thumbs[i] = thumb;
            thumbMs += sw.Elapsed.TotalMilliseconds;
            _metrics.Time("fingerprint_ms", sw.Elapsed.TotalMilliseconds);
        }
        var run = new HashSet<int>(_planner.Plan(candidates));

        // Kept findings survive only where their tile was judged unchanged; changed tiles will find again.
        var carried = new Dictionary<int, HashSet<Finding>>();
        for (var i = 0; i < all.Count; i++)
        {
            var (frame, pass, _) = all[i];
            if (pass.WholeFrame || !_planner.Unchanged.Contains(i) || !_kept.TryGetValue(frame.Index, out var kept)) continue;
            if (!carried.TryGetValue(frame.Index, out var set)) carried[frame.Index] = set = new HashSet<Finding>();
            foreach (var f in kept) if (pass.Region.Contains(f.Region) || pass.Region.IntersectsWith(f.Region)) set.Add(f);
        }
        foreach (var frame in _sweepFrames)
        {
            var keep = carried.TryGetValue(frame.Index, out var set) ? set.ToList() : new List<Finding>();
            _kept[frame.Index] = keep;
            _metrics.Count("findings_carried", keep.Count);
            if (keep.Count > 0) Apply(frame, keep);
        }
        for (var i = 0; i < all.Count; i++) if (run.Contains(i)) _sweep.Add(all[i]);

        _screensExamined += _sweepFrames.Count;
        _metrics.Count("sweeps");
        _metrics.Count("screens_captured", _sweepFrames.Count);
        _metrics.Count("passes_planned", candidates.Count);
        _metrics.Count("passes_run", _planner.Ran);
        _metrics.Count("passes_skipped", _planner.Skipped);
        _metrics.Count("passes_unchanged", _planner.Unchanged.Count);
        if (_planner.Motion) _metrics.Count("motion_sweeps");
        if (gameMode) _metrics.Count("game_sweeps");
        if (onBattery) _metrics.Count("battery_sweeps");
        _log.Append(new
        {
            t = Iso.Format(DateTimeOffset.UtcNow), kind = "sweep", screens = _sweepFrames.Count,
            capture_ms = Math.Round(captureMs, 1), fingerprint_ms = Math.Round(thumbMs, 1),
            scroll_px = shifts.Values.ToArray(), planned = candidates.Count, run = _planner.Ran,
            skipped = _planner.Skipped, unchanged = _planner.Unchanged.Count, motion = _planner.Motion,
            game = gameMode, battery = onBattery,
        });

        var intervalMs = Math.Max(1, _config?.Policy?.WholeScreenIntervalS ?? 5) * 1000;
        _scheduler.Configure(intervalMs, Math.Max(1, _sweep.Count), gameMode, onBattery);
        _timer.Interval = _scheduler.TickMs;
        var detail = $"{_planner.Ran} run, {_planner.Skipped} skipped";
        if (shifts.Values.Any(v => v != 0)) detail += ", scrolled";
        if (_planner.Motion) detail += ", motion";
        if (gameMode) detail += ", game";
        if (onBattery) detail += ", battery";
        if (detail != Detail) { Detail = detail; StatusChanged?.Invoke(); }
    }

    /// <summary>Once a minute: the window's tallies go to the local log and to the service for the next heartbeat.</summary>
    private async Task MaybeFlushMetrics()
    {
        if (DateTime.UtcNow - _lastFlush < TimeSpan.FromMinutes(1)) return;
        _lastFlush = DateTime.UtcNow;
        var snapshot = _metrics.Take();
        if (snapshot.IsEmpty) return;
        _log.Append(new { t = Iso.Format(DateTimeOffset.UtcNow), kind = "minute", snapshot.WindowS, snapshot.Counters, snapshot.Timings });
        try { await _pipe.SendAsync(new MetricsMessage(snapshot)); }
        catch (Exception) { /* the service is away; the local log still has it */ }
    }

    /// <summary>Inference off the UI thread; in game mode on a below-normal thread so a game keeps its cores.</summary>
    private static Task<List<Finding>> RunPass(FramePipeline pipeline, Bitmap frame, Pass pass, bool gameMode)
    {
        if (!gameMode) return Task.Run(() => pipeline.ExaminePass(frame, pass));
        var tcs = new TaskCompletionSource<List<Finding>>();
        var thread = new Thread(() =>
        {
            try { tcs.SetResult(pipeline.ExaminePass(frame, pass)); }
            catch (Exception ex) { tcs.SetException(ex); }
        }) { IsBackground = true, Priority = ThreadPriority.BelowNormal, Name = "watchman-game-pass" };
        thread.Start();
        return tcs.Task;
    }

    private void Apply(ScreenFrame frame, List<Finding> findings)
    {
        var bounds = frame.Bounds;
        var overlay = _overlays.For(bounds);
        // Report only (cover_on_device off): nothing is covered or blocked, findings are still reported.
        var coverOnDevice = _config?.Policy?.CoverOnDevice ?? true;
        var area = findings.Sum(f => (double)f.Region.Width * f.Region.Height) / ((double)bounds.Width * bounds.Height);
        var blocked = coverOnDevice && area > 0.4;
        var patches = findings.Select(f =>
        {
            using var ms = new MemoryStream(f.Jpeg);
            return (f.Region, (Bitmap)Image.FromStream(ms));
        }).ToList();
        if (coverOnDevice && (patches.Count > 0 || blocked || overlay.HasPatches))
            overlay.SetPatches(patches, TimeSpan.FromMilliseconds(_scheduler.IntervalMs * 1.5), blocked);
        foreach (var (_, bmp) in patches) bmp.Dispose();
        if (findings.Count == 0) return;
        var (app, title) = Native.Foreground();
        var threshold = _config?.Policy?.Thresholds ?? new Thresholds(0.8, 0.6, 0.55);
        var knownBad = _config?.Policy?.KnownBadHashes ?? new List<string>();
        foreach (var f in findings)
        {
            var isKnownBad = knownBad.Any(k => DHash.Matches(k, f.Hash));
            if (_recentHashes.TryGetValue(f.Hash, out var seen) && DateTime.UtcNow - seen < TimeSpan.FromMinutes(10)) continue;
            _recentHashes[f.Hash] = DateTime.UtcNow;
            _ = _pipe.SendAsync(new FindingMessage(f.Hash, Math.Max(f.Score, isKnownBad ? threshold.Cover : 0), f.Labels,
                f.Region.X, f.Region.Y, f.Region.Width, f.Region.Height, bounds.Width, bounds.Height, app, title,
                Convert.ToBase64String(f.Jpeg), blocked, coverOnDevice));
        }
        if (_recentHashes.Count > 2000)
        {
            foreach (var k in _recentHashes.Where(kv => DateTime.UtcNow - kv.Value > TimeSpan.FromMinutes(10)).Select(kv => kv.Key).ToList())
                _recentHashes.Remove(k);
        }
    }

    private async Task MaybeStats()
    {
        if (DateTime.UtcNow - _lastStats <= TimeSpan.FromHours(24)) return;
        await _pipe.SendAsync(new StatsMessage(_screensExamined, _imagesScored, (DateTime.UtcNow - _statsSince).TotalMinutes));
        _screensExamined = 0; _imagesScored = 0; _statsSince = DateTime.UtcNow; _lastStats = DateTime.UtcNow;
    }

    public void Dispose()
    {
        _timer.Dispose();
        foreach (var f in _sweepFrames) f.Dispose();
        _gpuDetector?.Dispose();
        _cpuDetector?.Dispose();
    }
}
