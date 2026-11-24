namespace Watchman.Service;

public static class Paths
{
    public static string DataDir => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "Watchman");
    public static string ModelsDir => Path.Combine(DataDir, "models");
    public static string UpdatesDir => Path.Combine(DataDir, "updates");
    public static string HelperExe => FindHelper(AppContext.BaseDirectory);

    /// <summary>The installer puts the service and the helper in sibling folders
    /// (Program Files\Watchman\service and \helper); a development build may have them side by side.</summary>
    public static string FindHelper(string serviceDir)
    {
        var beside = Path.Combine(serviceDir, "WatchmanHelper.exe");
        if (File.Exists(beside)) return beside;
        return Path.GetFullPath(Path.Combine(serviceDir, "..", "helper", "WatchmanHelper.exe"));
    }
}
