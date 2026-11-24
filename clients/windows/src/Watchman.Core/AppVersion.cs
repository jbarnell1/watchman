using System.Reflection;

namespace Watchman.Core;

/// <summary>The version this build was stamped with: the release tag, passed as -p:Version by the
/// release workflow (ADR-059). Builds from source report Directory.Build.props.</summary>
public static class AppVersion
{
    public static string Value { get; } = Read();

    /// <summary>True when <paramref name="candidate"/> is a later release than <paramref name="current"/>:
    /// 0.2.0-rc.1 &lt; 0.2.0 &lt; 0.2.1. Anything that is not a version is never newer.</summary>
    public static bool IsNewer(string candidate, string current)
    {
        var a = Parse(candidate);
        var b = Parse(current);
        if (a is null || b is null) return false;
        var (av, apre) = a.Value;
        var (bv, bpre) = b.Value;
        var c = av.CompareTo(bv);
        if (c != 0) return c > 0;
        if (apre == bpre) return false;
        if (apre is null) return true;      // the release is newer than its candidates
        if (bpre is null) return false;
        return string.CompareOrdinal(apre, bpre) > 0;
    }

    private static (Version, string?)? Parse(string v)
    {
        var m = System.Text.RegularExpressions.Regex.Match(v.Trim(), @"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.]+))?$");
        if (!m.Success) return null;
        return (new Version(int.Parse(m.Groups[1].Value), int.Parse(m.Groups[2].Value), int.Parse(m.Groups[3].Value)),
            m.Groups[4].Success ? m.Groups[4].Value : null);
    }

    private static string Read()
    {
        var v = typeof(AppVersion).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion ?? "0.0.0";
        var plus = v.IndexOf('+');  // the SDK appends +commit
        return plus >= 0 ? v[..plus] : v;
    }
}
