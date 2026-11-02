namespace Watchman.Core;

public interface IClock
{
    DateTimeOffset Now { get; }
}

public sealed class SystemClock : IClock
{
    public DateTimeOffset Now => DateTimeOffset.UtcNow;
}

public sealed class FakeClock : IClock
{
    public DateTimeOffset Now { get; set; } = new(2026, 11, 1, 12, 0, 0, TimeSpan.Zero);
    public void Advance(TimeSpan by) => Now += by;
}

public static class Iso
{
    /// <summary>RFC 3339 UTC with second precision, as the API contract uses.</summary>
    public static string Format(DateTimeOffset t) => t.ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss'Z'");

    public static DateTimeOffset Parse(string s) => DateTimeOffset.Parse(s, null, System.Globalization.DateTimeStyles.AssumeUniversal);
}
