using System.Text.Json;
using System.Text.Json.Serialization;

namespace Watchman.Core;

// Messages between the SYSTEM service and the per-user helper over the named pipe \\.\pipe\watchman.
// One JSON object per line, both directions. The helper opens the connection.

public static class Pipe
{
    public const string Name = "watchman";
}

[JsonPolymorphic(TypeDiscriminatorPropertyName = "type")]
[JsonDerivedType(typeof(HelloMessage), "hello")]
[JsonDerivedType(typeof(StateMessage), "state")]
[JsonDerivedType(typeof(FindingMessage), "finding")]
[JsonDerivedType(typeof(StatsMessage), "stats")]
[JsonDerivedType(typeof(MetricsMessage), "metrics")]
[JsonDerivedType(typeof(UnlockMessage), "unlock")]
[JsonDerivedType(typeof(RequestUnlockMessage), "request_unlock")]
public abstract record HelperMessage;

public sealed record HelloMessage(string Version, string User) : HelperMessage;
public sealed record StateMessage(bool Capture, bool Overlay, int Monitors) : HelperMessage;
public sealed record FindingMessage(string Hash, double Score, List<string> Labels, int X, int Y, int W, int H,
    int ScreenW, int ScreenH, string App, string? Title, string JpegBase64, bool Blocked, bool Covered = true) : HelperMessage;
public sealed record StatsMessage(int ScreensExamined, int ImagesScored, double MinutesProtected) : HelperMessage;
/// <summary>A minute of the helper's work (ADR-025); the service adds it to the next heartbeat.</summary>
public sealed record MetricsMessage(MetricsSnapshot Snapshot) : HelperMessage;
public sealed record UnlockMessage(string Code) : HelperMessage;
public sealed record RequestUnlockMessage(int Minutes, string Reason) : HelperMessage;

[JsonPolymorphic(TypeDiscriminatorPropertyName = "type")]
[JsonDerivedType(typeof(ConfigMessage), "config")]
[JsonDerivedType(typeof(ResultMessage), "result")]
public abstract record ServiceMessage;

/// <summary>Everything the helper needs: paths to verified models and the live policy.</summary>
public sealed record ConfigMessage(bool Paired, string? ServerName, Policy? Policy, string? DetectorPath, ModelEntry? Detector,
    string? PausedUntil, string? SmallDetectorPath = null, ModelEntry? SmallDetector = null) : ServiceMessage;

public sealed record ResultMessage(string For, bool Ok, string? Message) : ServiceMessage;

public static class Wire
{
    public static readonly JsonSerializerOptions Options = new(Json.Options) { WriteIndented = false };
    public static string Encode<T>(T msg) => JsonSerializer.Serialize(msg, Options);
    public static T? Decode<T>(string line) => JsonSerializer.Deserialize<T>(line, Options);
}
