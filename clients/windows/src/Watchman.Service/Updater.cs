using System.Diagnostics;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text.RegularExpressions;
using Microsoft.Extensions.Logging;
using Watchman.Core;

namespace Watchman.Service;

public sealed record UpdateAttempt(string Version, DateTimeOffset At);

/// <summary>Installs the Windows client the household server carries when it is newer than this one
/// (ADR-061). The heartbeat response names the MSI and its SHA-256; the file is checked against that
/// before msiexec runs it silently. The install stops this service, replaces the files and starts the
/// new service, which then restarts the helper.</summary>
public sealed partial class Updater(ServiceState state, ILogger log)
{
    public static readonly TimeSpan RetryAfter = TimeSpan.FromHours(6);
    private const long MaxBytes = 200L << 20;

    public string Directory { get; init; } = Paths.UpdatesDir;
    /// <summary>Only SYSTEM and administrators may write where the MSI waits for msiexec. Off in tests.</summary>
    public bool LockDown { get; init; } = true;
    public string CurrentVersion { get; init; } = AppVersion.Value;
    /// <summary>Starts the installer for (msi, log) and returns at once.</summary>
    public Action<string, string> Install { get; init; } = StartMsiexec;

    /// <summary>True when an install was started.</summary>
    public async Task<bool> ConsiderAsync(Api api, UpdateOffer? offer)
    {
        if (offer is null || !AppVersion.IsNewer(offer.Version, CurrentVersion)) return false;
        var file = offer.Files.FirstOrDefault(f => f.Variant == "x64" && MsiName().IsMatch(f.Name));
        if (file is null || file.Size > MaxBytes) return false;
        var last = state.LastUpdateAttempt;
        if (last is not null && last.Version == offer.Version && state.Clock.Now - last.At < RetryAfter) return false;
        state.LastUpdateAttempt = new UpdateAttempt(offer.Version, state.Clock.Now);

        var bytes = await api.BytesAsync(file.Url);
        var digest = Convert.ToHexStringLower(SHA256.HashData(bytes));
        if (!string.Equals(digest, file.Sha256, StringComparison.OrdinalIgnoreCase))
        {
            log.LogWarning("update {Version}: {Name} does not match its SHA-256; not installed", offer.Version, file.Name);
            return false;
        }
        Prepare();
        var msi = Path.Combine(Directory, file.Name);
        await File.WriteAllBytesAsync(msi, bytes);
        log.LogWarning("installing Watchman {Version} from the household server", offer.Version);
        Install(msi, Path.Combine(Directory, $"install-{offer.Version}.log"));
        return true;
    }

    private void Prepare()
    {
        var dir = System.IO.Directory.CreateDirectory(Directory);
        if (LockDown)
        {
            // ProgramData lets ordinary users create files in new folders; this one is closed to them,
            // so nobody can swap the MSI between the hash check and msiexec.
            var acl = new DirectorySecurity();
            acl.SetAccessRuleProtection(isProtected: true, preserveInheritance: false);
            foreach (var sid in new[] { WellKnownSidType.LocalSystemSid, WellKnownSidType.BuiltinAdministratorsSid })
                acl.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(sid, null), FileSystemRights.FullControl,
                    InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow));
            dir.SetAccessControl(acl);
        }
        foreach (var old in dir.GetFiles("*.msi")) old.Delete();
    }

    private static void StartMsiexec(string msi, string logPath)
    {
        var psi = new ProcessStartInfo(Path.Combine(Environment.SystemDirectory, "msiexec.exe"))
        {
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        foreach (var arg in new[] { "/i", msi, "/qn", "/norestart", "/l*v", logPath }) psi.ArgumentList.Add(arg);
        Process.Start(psi)?.Dispose();
    }

    [GeneratedRegex(@"^watchman-windows-[0-9A-Za-z.\-]+-x64\.msi$")]
    private static partial Regex MsiName();
}
