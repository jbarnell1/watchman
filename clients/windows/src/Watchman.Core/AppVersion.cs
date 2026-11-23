using System.Reflection;

namespace Watchman.Core;

/// <summary>The version this build was stamped with: the release tag, passed as -p:Version by the
/// release workflow (ADR-059). Builds from source report Directory.Build.props.</summary>
public static class AppVersion
{
    public static string Value { get; } = Read();

    private static string Read()
    {
        var v = typeof(AppVersion).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion ?? "0.0.0";
        var plus = v.IndexOf('+');  // the SDK appends +commit
        return plus >= 0 ? v[..plus] : v;
    }
}
