using System.Drawing;
using Watchman.Core;
using Xunit;

namespace Watchman.Core.Tests;

public class VisionTests
{
    private static readonly ModelInput Detector = new("images", 64, "NCHW", "RGB", 255, new[] { 0.0, 0, 0 }, new[] { 1.0, 1, 1 }, "letterbox");
    private static readonly ModelOutput Output = new("output0", "yolov8", new List<string> { "A", "B", "C" }, null, new List<string> { "B" });

    [Fact]
    public void LetterboxKeepsAspectAndPads()
    {
        using var img = new Bitmap(200, 100);
        using (var g = Graphics.FromImage(img)) g.Clear(Color.White);
        var (tensor, lb) = Vision.LetterboxTensor(img, Detector);
        Assert.Equal(3 * 64 * 64, tensor.Length);
        Assert.Equal(0.32, lb.Scale, 5);
        Assert.Equal(0, lb.PadX);
        Assert.Equal(16, lb.PadY);
        Assert.Equal(1.0f, tensor[20 * 64 + 10], 2);              // inside the image, R plane
        Assert.Equal(114f / 255f, tensor[2 * 64 + 10], 2);         // padding row
    }

    [Fact]
    public void ClassifierTensorNormalises()
    {
        var input = new ModelInput("input", 8, "NCHW", "RGB", 255, new[] { 0.5, 0.5, 0.5 }, new[] { 0.5, 0.5, 0.5 }, "bicubic");
        using var img = new Bitmap(30, 20);
        using (var g = Graphics.FromImage(img)) g.Clear(Color.FromArgb(255, 0, 128));
        var t = Vision.ClassifierTensor(img, input);
        const int centre = 4 * 8 + 4; // GDI resampling softens the outermost pixels of a flat image
        Assert.Equal(1.0f, t[centre], 2);
        Assert.Equal(-1.0f, t[64 + centre], 2);
        Assert.Equal((float)((128 / 255.0 - 0.5) / 0.5), t[128 + centre], 2);
    }

    [Fact]
    public void YoloDecodeWithNmsAndUnletterbox()
    {
        const int classes = 3, anchors = 3;
        var raw = new float[(4 + classes) * anchors];
        void Set(int a, float[] vals) { for (var r = 0; r < vals.Length; r++) raw[r * anchors + a] = vals[r]; }
        Set(0, new[] { 100, 100, 40, 40, 0.1f, 0.9f, 0f });
        Set(1, new[] { 104, 102, 40, 40, 0.1f, 0.6f, 0f });
        Set(2, new[] { 10, 10, 10, 10, 0.2f, 0f, 0f });
        var boxes = Vision.DecodeYolo(raw, new[] { 1, 4 + classes, anchors }, Output, new Letterbox(0.5, 0, 20));
        var b = Assert.Single(boxes);
        Assert.Equal("B", b.Label);
        Assert.Equal(0.9, b.Score, 5);
        Assert.Equal((160.0, 120.0, 80.0, 80.0), (b.X, b.Y, b.W, b.H));
        Assert.Equal(0.9, Vision.ExplicitScore(boxes, Output), 5);
    }

    [Fact]
    public void TilesCoverTheFrameWithOverlap()
    {
        var tiles = Vision.Tiles(2560, 1440, 640);
        Assert.All(tiles, t => Assert.True(t.Right <= 2560 && t.Bottom <= 1440));
        Assert.Contains(tiles, t => t.X == 0 && t.Y == 0);
        Assert.Contains(tiles, t => t.Right == 2560 && t.Bottom == 1440);
        Assert.True(tiles.Count >= 12);
        Assert.Single(Vision.Tiles(300, 200, 640));
    }

    [Fact]
    public void MergeUnionsOverlappingBoxes()
    {
        var merged = FramePipeline.Merge(new List<Box>
        {
            new("B", 0.9, 10, 10, 50, 50), new("A", 0.5, 40, 40, 50, 50), new("B", 0.7, 300, 300, 20, 20),
        });
        Assert.Equal(2, merged.Count);
        var big = merged.First(m => m.Rect.X == 10);
        Assert.Equal(new Rectangle(10, 10, 80, 80), big.Rect);
        Assert.Equal(0.9, big.Score);
        Assert.Equal(new[] { "B", "A" }, big.Labels);
    }

    [Fact]
    public void PipelineCoversExplicitBoxesAndKeepsACrop()
    {
        var entry = new ModelEntry("detector_large", "d.onnx", "", "", 0, Detector, Output);
        var fake = new FakeDetector(entry)
        {
            Answer = frame => frame.Width == 256
                ? new List<Box> { new("B", 0.8, 100, 100, 40, 40), new("A", 0.99, 0, 0, 10, 10) }
                : new List<Box>(),
        };
        var pipeline = new FramePipeline(fake, () => new Thresholds(0.8, 0.6, 0.55));
        using var frame = new Bitmap(256, 128);
        using (var g = Graphics.FromImage(frame)) g.Clear(Color.Gray);
        var findings = pipeline.Examine(frame);
        var f = Assert.Single(findings);
        Assert.True(f.Region.Contains(new Rectangle(100, 100, 40, 28)));
        Assert.Equal(0.8, f.Score);
        Assert.NotEmpty(f.Jpeg);
        Assert.Equal(16, f.Hash.Length);
        Assert.True(fake.Calls > 1); // the whole frame plus tiles
    }
}

public class CoverageTests
{
    [Fact]
    public void ThePolicyLabelSetOverridesTheManifest()
    {
        var entry = new ModelEntry("detector_large", "d.onnx", "", "", 0,
            new ModelInput("images", 64, "NCHW", "RGB", 255, new[] { 0.0, 0, 0 }, new[] { 1.0, 1, 1 }, "letterbox"),
            new ModelOutput("output0", "yolov8", new List<string> { "BARE", "COVERED" }, null, new List<string> { "BARE" }));
        var fake = new FakeDetector(entry) { Answer = _ => new List<Box> { new("COVERED", 0.9, 10, 10, 30, 30) } };
        using var frame = new Bitmap(64, 64);
        var strict = new FramePipeline(fake, () => new Thresholds(0.8, 0.6, 0.55), () => new[] { "BARE", "COVERED" });
        Assert.Single(strict.Examine(frame));
        var plain = new FramePipeline(fake, () => new Thresholds(0.8, 0.6, 0.55));
        Assert.Empty(plain.Examine(frame));
    }
}

public class SchedulerTests
{
    [Fact]
    public void OneInferencePerTickAcrossTheInterval()
    {
        var s = new SweepScheduler(5000, 13);
        Assert.Equal(384, s.TickMs);
        Assert.True(s.SweepStarts);
        var order = Enumerable.Range(0, 14).Select(_ => s.Next()).ToList();
        Assert.Equal(0, order[0]);
        Assert.Equal(12, order[12]);
        Assert.Equal(0, order[13]);
    }

    [Fact]
    public void GameModeCollapsesToOnePassPerInterval()
    {
        var s = new SweepScheduler(5000, 13, gameMode: true);
        Assert.Equal(1, s.EffectiveCount);
        Assert.Equal(5000, s.TickMs);
        Assert.Equal(0, s.Next());
        Assert.Equal(0, s.Next());
        s.Configure(10000, 13, gameMode: false);
        Assert.Equal(769, s.TickMs);
    }

    [Fact]
    public void OnBatteryTheIntervalDoubles()
    {
        var s = new SweepScheduler(5000, 4, gameMode: false, onBattery: true);
        Assert.Equal(10000, s.IntervalMs);
        Assert.Equal(2500, s.TickMs);
        s.Configure(5000, 4, gameMode: false, onBattery: false);
        Assert.Equal(5000, s.IntervalMs);
    }

    [Fact]
    public void PlannerRunsOnlyWhatChangedAndCollapsesUnderMotion()
    {
        var planner = new SweepPlanner();
        var first = new List<Candidate>
        {
            new("0:whole", "0000000000000000", true), new("0:a", "1111111111111111", false),
            new("0:b", "2222222222222222", false), new("0:c", "3333333333333333", false),
        };
        Assert.Equal(new[] { 0, 1, 2, 3 }, planner.Plan(first)); // everything is new on the first sweep
        Assert.False(planner.Motion);

        // Nothing moved (one tile a bit off, within tolerance): nothing runs.
        var still = first.Select(c => c.Key == "0:b" ? c with { Fingerprint = "2222222222222223" } : c).ToList();
        Assert.Empty(planner.Plan(still));
        Assert.Equal(0, planner.Ran);
        Assert.Equal(4, planner.Skipped);

        // One tile changed: only that tile runs.
        var one = first.Select(c => c.Key == "0:c" ? c with { Fingerprint = "ffffffffffffffff" } : c).ToList();
        Assert.Equal(new[] { 3 }, planner.Plan(one));

        // Every tile changed (a video): motion mode, the whole frame alone runs.
        var video = first.Select((c, i) => c with { Fingerprint = $"{i + 7:x}{i + 7:x}{i + 7:x}{i + 7:x}aaaaaaaaaaaa" }).ToList();
        Assert.Equal(new[] { 0 }, planner.Plan(video));
        Assert.True(planner.Motion);
    }

    [Fact]
    public void FingerprintIsStableForAStillRegionAndDiffersWhenItChanges()
    {
        using var frame = new Bitmap(400, 300);
        using (var g = Graphics.FromImage(frame))
        {
            g.Clear(Color.White);
            g.FillRectangle(Brushes.Black, 20, 20, 100, 100);
        }
        var region = new Rectangle(0, 0, 200, 150);
        var a = DHash.Fingerprint(frame, region);
        Assert.Equal(a, DHash.Fingerprint(frame, region));
        using (var g = Graphics.FromImage(frame))
        {
            g.Clear(Color.Black);
            g.FillRectangle(Brushes.White, 20, 20, 100, 100);
        }
        Assert.True(DHash.Hamming(a, DHash.Fingerprint(frame, region)) > SweepPlanner.UnchangedBits);
    }

    [Fact]
    public void TimingPercentilesComeFromBucketsAndMerge()
    {
        var clock = new FakeClock();
        var m = new Metrics(clock);
        foreach (var ms in new[] { 3.0, 12, 14, 15, 30, 300 }) m.Time("inference_ms", ms);
        m.Count("passes_run", 6);
        clock.Advance(TimeSpan.FromSeconds(60));
        var s = m.Take();
        Assert.Equal(60, s.WindowS);
        var t = s.Timings["inference_ms"];
        Assert.Equal(6, t.N);
        Assert.Equal(16, t.Percentile(0.5));
        Assert.Equal(300, t.Percentile(1.0));
        Assert.Empty(m.Take().Counters);
        var other = new MetricsSnapshot { WindowS = 60 };
        other.Counters["passes_run"] = 4;
        s.Merge(other);
        Assert.Equal(10, s.Counters["passes_run"]);
        Assert.Equal(120, s.WindowS);
        var json = System.Text.Json.JsonSerializer.Serialize(s);
        Assert.Contains("\"window_s\":120", json);
        Assert.Contains("\"buckets\":[0,0,1,0,3,1,0,0,0,1,0,0]", json);
    }

    [Fact]
    public void MetricsLogRotatesAtItsLimit()
    {
        var dir = Path.Combine(Path.GetTempPath(), "wm-" + Guid.NewGuid().ToString("N"));
        var log = new MetricsLog(Path.Combine(dir, "metrics.jsonl"), maxBytes: 200);
        for (var i = 0; i < 20; i++) log.Append(new { i, kind = "sweep", padding = "xxxxxxxxxxxxxxxx" });
        Assert.True(File.Exists(log.Path + ".1"));
        Assert.True(new FileInfo(log.Path).Length <= 200);
        Directory.Delete(dir, true);
    }

    private static Bitmap Page(int width, int height, int offset)
    {
        // A tall page of uneven stripes and blocks, drawn with the viewport scrolled down by `offset`.
        var bmp = new Bitmap(width, height);
        using var g = Graphics.FromImage(bmp);
        g.Clear(Color.White);
        var rng = new Random(7);
        var y = 0;
        while (y < height + offset + 400)
        {
            var h = rng.Next(12, 70);
            var shade = rng.Next(0, 230);
            var x = rng.Next(0, width / 2);
            using var b = new SolidBrush(Color.FromArgb(shade, shade, shade));
            g.FillRectangle(b, x, y - offset, rng.Next(80, width - x), h);
            y += h + rng.Next(4, 30);
        }
        return bmp;
    }

    [Fact]
    public void ScrollIsEstimatedAndOnlyTheRevealedStripRuns()
    {
        using var before = Page(1280, 720, 0);
        using var after = Page(1280, 720, 240); // scrolled down 240 px
        var a = GrayThumb.From(before);
        var b = GrayThumb.From(after);
        var (dy, confidence) = ScrollEstimator.Estimate(a, b, b.Height / 2);
        Assert.InRange(dy * GrayThumb.Scale, 232, 248);
        Assert.True(confidence > 0.5);
        Assert.Equal((0, 0.0), ScrollEstimator.Estimate(a, a, a.Height / 2));

        // Tiles: the planner sees the content that scrolled into each place, so only the bottom strip runs.
        var shift = dy * GrayThumb.Scale;
        var tiles = Vision.Tiles(1280, 720, 320);
        var planner = new SweepPlanner();
        planner.Plan(tiles.Select(t => new Candidate($"{t.X},{t.Y}", a.Fingerprint(t)!, false)).ToList());
        var run = planner.Plan(tiles.Select(t => new Candidate($"{t.X},{t.Y}", b.Fingerprint(t)!, false, a.Fingerprint(t, shift))).ToList());
        Assert.False(planner.Motion);
        Assert.NotEmpty(run);
        Assert.All(run, i => Assert.True(tiles[i].Bottom > 720 - shift, $"tile {tiles[i]} ran but was only scrolled"));
    }

    [Fact]
    public void AFindingMovesWithThePageUntilItLeaves()
    {
        var f = new Finding(new Rectangle(100, 300, 80, 80), 0.9, new List<string>(), "0", Array.Empty<byte>());
        Assert.Equal(new Rectangle(100, 60, 80, 80), ScrollEstimator.Move(f, 240, new Size(1280, 720))!.Region);
        Assert.Null(ScrollEstimator.Move(f, 400, new Size(1280, 720)));
    }

    [Fact]
    public void PassesAreTheWholeFrameThenTiles()
    {
        var entry = new ModelEntry("detector_large", "d.onnx", "", "", 0,
            new ModelInput("images", 640, "NCHW", "RGB", 255, new[] { 0.0, 0, 0 }, new[] { 1.0, 1, 1 }, "letterbox"),
            new ModelOutput("output0", "yolov8", new List<string> { "A" }, null, new List<string> { "A" }));
        var pipeline = new FramePipeline(new FakeDetector(entry), () => new Thresholds(0.8, 0.6, 0.55));
        var passes = pipeline.Passes(2048, 1152); // 4 by 3 tiles of 640 at step 480
        Assert.True(passes[0].WholeFrame);
        Assert.Equal(13, passes.Count);
        Assert.Single(pipeline.Passes(300, 200));
    }
}
