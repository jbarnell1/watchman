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
                var seen = state.HelperSeenAt;
                var stale = seen is null || state.Clock.Now - seen > TimeSpan.FromSeconds(45);
                if (stale && !HelperRunning()) Launch();
            }
            catch (Exception ex)
            {
                log.LogWarning(ex, "launch helper");
            }
            await Task.Delay(TimeSpan.FromSeconds(15), ct);
        }
    }

    private static bool HelperRunning() => Process.GetProcessesByName("WatchmanHelper").Length > 0;

    private void Launch()
    {
        if (!File.Exists(Paths.HelperExe)) { log.LogWarning("helper not found at {Path}", Paths.HelperExe); return; }
        var session = WTSGetActiveConsoleSessionId();
        if (session == 0xFFFFFFFF) return; // nobody is signed in
        if (!WTSQueryUserToken(session, out var token)) { log.LogWarning("no user token for session {Session}", session); return; }
        try
        {
            if (!CreateEnvironmentBlock(out var env, token, false)) env = IntPtr.Zero;
            var si = new STARTUPINFO { cb = Marshal.SizeOf<STARTUPINFO>(), lpDesktop = "winsta0\\default" };
            var ok = CreateProcessAsUser(token, null, $"\"{Paths.HelperExe}\"", IntPtr.Zero, IntPtr.Zero, false,
                CREATE_UNICODE_ENVIRONMENT | CREATE_NEW_CONSOLE, env, Path.GetDirectoryName(Paths.HelperExe), ref si, out var pi);
            if (ok) { CloseHandle(pi.hProcess); CloseHandle(pi.hThread); log.LogInformation("helper started in session {Session}", session); }
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
