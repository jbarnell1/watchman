// The per-user helper: captures the screen, scores it, draws the blur overlay, shows the tray icon.
// Started by the service in the signed-in user's session and restarted when it goes away.
//
//   WatchmanHelper.exe selftest <path to detector .onnx> [<manifest.json>]
// captures every monitor once, runs the detector (DirectML when available) and prints timings.

using System.Diagnostics;
using System.Text.Json;
using Watchman.Core;
using Watchman.Helper;

if (args.Length >= 2 && args[0] == "selftest")
{
    return SelfTest(args[1], args.Length > 2 ? args[2] : null);
}

using var single = new Mutex(true, "Local\\WatchmanHelper", out var first);
if (!first) return 0;

ApplicationConfiguration.Initialize();
Application.Run(new TrayApp());
return 0;

static int SelfTest(string modelPath, string? manifestPath)
{
    manifestPath ??= Path.Combine(Path.GetDirectoryName(modelPath)!, "manifest.json");
    var manifest = JsonSerializer.Deserialize<ModelsManifest>(File.ReadAllText(manifestPath), Json.Options)!;
    var entry = manifest.Models.First(m => m.File == Path.GetFileName(modelPath));
    var sw = Stopwatch.StartNew();
    using var detector = new OnnxDetector(entry, modelPath);
    Console.WriteLine($"{entry.Role} loaded in {sw.ElapsedMilliseconds} ms, provider {detector.Provider}");
    var pipeline = new FramePipeline(detector, () => new Thresholds(0.8, 0.6, 0.55));
    var capture = new GdiCapture();
    for (var i = 0; i < capture.Screens.Count; i++)
    {
        sw.Restart();
        using var frame = capture.Capture(i);
        var captureMs = sw.ElapsedMilliseconds;
        sw.Restart();
        var thumb = GrayThumb.From(frame.Image);
        var thumbMs = sw.Elapsed.TotalMilliseconds;
        sw.Restart();
        var fingerprints = pipeline.Passes(frame.Image.Width, frame.Image.Height).Select(p => thumb.Fingerprint(p.Region)).Count();
        var fpMs = sw.Elapsed.TotalMilliseconds;
        // The worst case: a real screen against itself scrolled by 30 thumbnail rows, so every shift is tried.
        var moved = new byte[thumb.Pixels.Length];
        Array.Copy(thumb.Pixels, 30 * thumb.Width, moved, 0, thumb.Pixels.Length - 30 * thumb.Width);
        var shifted = new GrayThumb(thumb.Width, thumb.Height, moved);
        sw.Restart();
        var (estimate, _) = ScrollEstimator.Estimate(thumb, shifted, thumb.Height / 2);
        var scrollMs = sw.Elapsed.TotalMilliseconds;
        Console.WriteLine($"screen {i}: scroll test found {estimate * GrayThumb.Scale} px of 240");
        Console.WriteLine($"screen {i}: thumbnail {thumbMs:F1} ms, {fingerprints} fingerprints {fpMs:F1} ms, scroll estimate {scrollMs:F1} ms");
        var passes = pipeline.Passes(frame.Image.Width, frame.Image.Height);
        var times = new List<long>();
        var findings = new List<Finding>();
        foreach (var pass in passes)
        {
            sw.Restart();
            findings.AddRange(pipeline.ExaminePass(frame.Image, pass));
            times.Add(sw.ElapsedMilliseconds);
        }
        var scheduler = new SweepScheduler(5000, passes.Count);
        Console.WriteLine($"screen {i} {frame.Bounds.Width}x{frame.Bounds.Height}: capture {captureMs} ms; {passes.Count} passes, " +
                          $"first {times[0]} ms, then {string.Join("/", times.Skip(1))} ms; one every {scheduler.TickMs} ms; {findings.Count} finding(s)");
        foreach (var f in findings)
            Console.WriteLine($"  {f.Region} score {f.Score:F2} {string.Join(",", f.Labels)} hash {f.Hash} jpeg {f.Jpeg.Length} bytes");
        if (i == 0)
        {
            var smallEntry = manifest.Models.FirstOrDefault(m => m.Role == "detector_small");
            var smallPath = smallEntry is null ? null : Path.Combine(Path.GetDirectoryName(modelPath)!, smallEntry.File);
            if (smallEntry is not null && smallPath is not null && File.Exists(smallPath))
            {
                using var cpu = new OnnxDetector(smallEntry, smallPath, tryDirectML: false);
                var cpuPipeline = new FramePipeline(cpu, () => new Thresholds(0.8, 0.6, 0.55));
                var whole = cpuPipeline.Passes(frame.Image.Width, frame.Image.Height)[0];
                cpuPipeline.ExaminePass(frame.Image, whole);
                sw.Restart();
                cpuPipeline.ExaminePass(frame.Image, whole);
                Console.WriteLine($"game mode: whole-frame pass with {cpu.Entry.File} on the CPU in {sw.ElapsedMilliseconds} ms (one per interval)");
            }
        }
    }
    return 0;
}
