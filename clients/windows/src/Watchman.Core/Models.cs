using System.Text.Json;
using System.Text.Json.Serialization;

namespace Watchman.Core;

// Shapes from docs/api-contract.md and models/manifest.json.

public static class Json
{
    public static readonly JsonSerializerOptions Options = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = JsonIgnoreCondition.Never,
        NumberHandling = JsonNumberHandling.AllowReadingFromString,
    };
}

public sealed record Thresholds(double Cover, double Flag, [property: JsonPropertyName("detector_cover")] double DetectorCover,
    [property: JsonPropertyName("ask")] double? Ask = null);

public sealed record Policy(
    int Version,
    Thresholds Thresholds,
    [property: JsonPropertyName("strict_mode")] bool StrictMode,
    [property: JsonPropertyName("coverage")] string? Coverage,
    [property: JsonPropertyName("explicit_labels")] List<string>? ExplicitLabels,
    [property: JsonPropertyName("risky_apps")] List<string> RiskyApps,
    [property: JsonPropertyName("blocked_apps")] List<string> BlockedApps,
    [property: JsonPropertyName("protected_browser")] string ProtectedBrowser,
    [property: JsonPropertyName("sample_interval_s")] int SampleIntervalS,
    [property: JsonPropertyName("whole_screen_interval_s")] int WholeScreenIntervalS,
    [property: JsonPropertyName("paused_until")] string? PausedUntil,
    [property: JsonPropertyName("known_bad_hashes")] List<string> KnownBadHashes,
    [property: JsonPropertyName("cover_on_device")] bool? CoverOnDevice = null,
    [property: JsonPropertyName("ask_server")] bool? AskServer = null)
{
    public bool IsPaused(IClock clock) => PausedUntil is not null && Iso.Parse(PausedUntil) > clock.Now;

    /// <summary>Used when no policy was ever fetched, so protection never waits on the server: the
    /// contract's defaults, nothing blocked, not paused.</summary>
    public static Policy Defaults { get; } = new(
        Version: 0, Thresholds: new Thresholds(0.85, 0.85, 0.55, 0.60), StrictMode: true, Coverage: null, ExplicitLabels: null,
        RiskyApps: new(), BlockedApps: new(), ProtectedBrowser: "", SampleIntervalS: 180, WholeScreenIntervalS: 5,
        PausedUntil: null, KnownBadHashes: new());
}

public sealed record Pairing(string Server, string Token, [property: JsonPropertyName("device_id")] string DeviceId,
    [property: JsonPropertyName("person_id")] string PersonId, [property: JsonPropertyName("server_name")] string ServerName);

public sealed record ModelInput(string Name, int Size, string Layout, string Color, double Scale, double[] Mean, double[] Std, string Resize);

public sealed record ModelOutput(string Name, string Kind, List<string> Labels, string? Positive,
    [property: JsonPropertyName("explicit_labels")] List<string>? ExplicitLabels);

public sealed record ModelEntry(string Role, string File, string Url, string Sha256, long Bytes, ModelInput Input, ModelOutput Output);

public sealed record ModelsManifest(int Version, List<ModelEntry> Models);

public sealed class WatchmanEvent
{
    public string Id { get; set; } = Guid.NewGuid().ToString();
    public string Type { get; set; } = "image_covered";
    [JsonPropertyName("occurred_at")] public string OccurredAt { get; set; } = "";
    public string? App { get; set; }
    public string? Url { get; set; }
    public string? Title { get; set; }
    public string Action { get; set; } = "covered";
    public Dictionary<string, object?>? Scores { get; set; }
    public Dictionary<string, int>? Region { get; set; }
    public string? Hash { get; set; }
    [JsonPropertyName("has_image")] public bool HasImage { get; set; }
    public Dictionary<string, object?> Detail { get; set; } = new();
}

public sealed record RejectedEvent(string? Id, string? Reason);

public sealed record EventsResponse(List<string> Accepted, List<string> Duplicates, List<string> Upload,
    List<RejectedEvent>? Rejected = null);

public sealed record HeartbeatResponse([property: JsonPropertyName("policy_version")] int PolicyVersion,
    [property: JsonPropertyName("paused_until")] string? PausedUntil, [property: JsonPropertyName("server_time")] string ServerTime);

/// <summary>What the helper tells the service about itself, and what the service puts in heartbeats.</summary>
public sealed record ProtectionState(bool Protection, string Capture, bool? Overlay)
{
    public static ProtectionState Off => new(false, "denied", false);
}
