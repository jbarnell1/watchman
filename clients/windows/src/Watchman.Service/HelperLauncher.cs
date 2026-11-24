using System.Diagnostics;
using System.Runtime.InteropServices;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace Watchman.Service;

/// <summary>Starts the helper in the console user's session and starts it again when it goes away.
/// The helper and the service watch each other: this is the service's half (plan, "Tamper").</summary>
public sealed class HelperLauncher(ServiceState state, ILogger<HelperLauncher> log) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            try
            {
                if (state.TakeHelperRestart() is int pid) Restart(pid);
                var seen = state.HelperSeenAt;
                var stale = seen is null || state.Clock.Now - seen > TimeSpan.FromSeconds(45);
                if (stale && !HelperRunning()) Launch();
            }
            catch (Exception ex)
            {
                log.LogWarning(ex, "launch helper");
            }
            await state.HelperWake.WaitAsync(TimeSpan.FromSeconds(15), ct);
        }
    }

    /// <summary>Stops the outdated helper and starts the installed one. The helper allows one copy of
    /// itself, so the old one must be gone first: the process that connected, or failing that every
    /// running helper. Each step is logged as a warning so the event log shows what happened.
    /// The connected process is stopped whatever its name: the installer renames a running helper's
    /// file to its rollback copy (Config.Msi\*.rbf), and the process then goes by that name.</summary>
    private void Restart(int pid)
    {
        var stopped = Stop(pid, "connected", anyName: true);
        if (!stopped)
            foreach (var p in Process.GetProcessesByName("WatchmanHelper")) stopped |= Stop(p.Id, "running");
        if (!stopped && HelperRunning()) { log.LogWarning("could not stop the outdated helper; it keeps running"); return; }
        Launch(alwaysLog: true);
    }

    private bool Stop(int pid, string which, bool anyName = false)
    {
        try
        {
            using var p = Process.GetProcessById(pid);
            if (!anyName && !string.Equals(p.ProcessName, "WatchmanHelper", StringComparison.OrdinalIgnoreCase))
            {
                log.LogWarning("the {Which} process {Pid} is {Name}, not the helper", which, pid, p.ProcessName);
                return false;
            }
            p.Kill();
            var gone = p.WaitForExit(10_000);
            log.LogWarning("stopped the {Which} helper {Pid} ({Name}): {Result}", which, pid, p.ProcessName,
                gone ? "exited" : "still running after 10 s");
            return gone;
        }
        catch (ArgumentException)
        {
            log.LogWarning("the {Which} helper {Pid} is not running", which, pid);
            return false;
        }
        catch (Exception ex)
        {
            log.LogWarning(ex, "stop the {Which} helper {Pid}", which, pid);
            return false;
        }
    }

    private static bool HelperRunning() => Process.GetProcessesByName("WatchmanHelper").Length > 0;

    private void Launch(bool alwaysLog = false)
    {
        if (!File.Exists(Paths.HelperExe)) { log.LogWarning("helper not found at {Path}", Paths.HelperExe); return; }
        var session = WTSGetActiveConsoleSessionId();
        if (session == 0xFFFFFFFF)  // nobody is signed in
        {
            if (alwaysLog) log.LogWarning("nobody is signed in at the console; the helper starts at the next sign-in");
            return;
        }
        if (!WTSQueryUserToken(session, out var token)) { log.LogWarning("no user token for session {Session}", session); return; }
        try
        {
            if (!CreateEnvironmentBlock(out var env, token, false)) env = IntPtr.Zero;
            var si = new STARTUPINFO { cb = Marshal.SizeOf<STARTUPINFO>(), lpDesktop = "winsta0\\default" };
            var ok = CreateProcessAsUser(token, null, $"\"{Paths.HelperExe}\"", IntPtr.Zero, IntPtr.Zero, false,
                CREATE_UNICODE_ENVIRONMENT | CREATE_NEW_CONSOLE, env, Path.GetDirectoryName(Paths.HelperExe), ref si, out var pi);
            if (ok)
            {
                CloseHandle(pi.hProcess); CloseHandle(pi.hThread);
                if (alwaysLog) log.LogWarning("helper restarted as {Pid} in session {Session}", pi.dwProcessId, session);
                else log.LogInformation("helper started in session {Session}", session);
            }
            else log.LogWarning("CreateProcessAsUser failed: {Error}", Marshal.GetLastWin32Error());
            if (env != IntPtr.Zero) DestroyEnvironmentBlock(env);
        }
        finally
        {
            CloseHandle(token);
        }
    }

    private const uint CREATE_UNICODE_ENVIRONMENT = 0x00000400;
    private const uint CREATE_NEW_CONSOLE = 0x00000010;

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct STARTUPINFO
    {
        public int cb; public string? lpReserved; public string? lpDesktop; public string? lpTitle;
        public int dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags;
        public short wShowWindow, cbReserved2; public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct PROCESS_INFORMATION { public IntPtr hProcess, hThread; public int dwProcessId, dwThreadId; }

    [DllImport("kernel32.dll")] private static extern uint WTSGetActiveConsoleSessionId();
    [DllImport("wtsapi32.dll", SetLastError = true)] private static extern bool WTSQueryUserToken(uint sessionId, out IntPtr token);
    [DllImport("userenv.dll", SetLastError = true)] private static extern bool CreateEnvironmentBlock(out IntPtr env, IntPtr token, bool inherit);
    [DllImport("userenv.dll", SetLastError = true)] private static extern bool DestroyEnvironmentBlock(IntPtr env);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool CloseHandle(IntPtr handle);
    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern bool CreateProcessAsUser(IntPtr token, string? app, string cmd, IntPtr pa, IntPtr ta, bool inherit,
        uint flags, IntPtr env, string? cwd, ref STARTUPINFO si, out PROCESS_INFORMATION pi);
}
