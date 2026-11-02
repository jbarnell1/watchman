using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

namespace Watchman.Helper;

internal static class Native
{
    public const uint WDA_EXCLUDEFROMCAPTURE = 0x00000011;
    public const int GWL_EXSTYLE = -20;
    public const int WS_EX_TRANSPARENT = 0x20;
    public const int WS_EX_LAYERED = 0x80000;
    public const int WS_EX_TOOLWINDOW = 0x80;
    public const int WS_EX_NOACTIVATE = 0x08000000;
    public const int WS_EX_TOPMOST = 0x8;

    [DllImport("user32.dll", SetLastError = true)] public static extern bool SetWindowDisplayAffinity(IntPtr hwnd, uint affinity);
    [DllImport("user32.dll")] public static extern int GetWindowLong(IntPtr hwnd, int index);
    [DllImport("user32.dll")] public static extern int SetWindowLong(IntPtr hwnd, int index, int value);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr hwnd, StringBuilder text, int count);

    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left, Top, Right, Bottom; }

    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hwnd, out RECT rect);

    private static readonly HashSet<string> NotGames = new(StringComparer.OrdinalIgnoreCase)
    {
        "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "explorer.exe", "WatchmanHelper.exe",
        "Discord.exe", "Teams.exe", "ms-teams.exe", "slack.exe", "Code.exe", "devenv.exe",
    };

    /// <summary>True when the window in front covers a whole monitor and is not a browser or desktop app:
    /// a game, a video player in full screen, a remote desktop. The sweep then goes gentle (ADR-018).</summary>
    public static bool IsGameInFront(IReadOnlyList<System.Drawing.Rectangle> screens)
    {
        try
        {
            var hwnd = GetForegroundWindow();
            if (hwnd == IntPtr.Zero || !GetWindowRect(hwnd, out var r)) return false;
            var rect = System.Drawing.Rectangle.FromLTRB(r.Left, r.Top, r.Right, r.Bottom);
            if (!screens.Any(s => rect.Contains(s))) return false;
            var (app, _) = Foreground();
            return !NotGames.Contains(app);
        }
        catch
        {
            return false;
        }
    }

    /// <summary>Process name and title of the window in front, for the event record.</summary>
    public static (string App, string? Title) Foreground()
    {
        try
        {
            var hwnd = GetForegroundWindow();
            if (hwnd == IntPtr.Zero) return ("desktop", null);
            GetWindowThreadProcessId(hwnd, out var pid);
            var name = pid == 0 ? "unknown" : Process.GetProcessById((int)pid).ProcessName + ".exe";
            var sb = new StringBuilder(256);
            GetWindowText(hwnd, sb, sb.Capacity);
            var title = sb.ToString();
            return (name, string.IsNullOrWhiteSpace(title) ? null : title);
        }
        catch
        {
            return ("unknown", null);
        }
    }
}
