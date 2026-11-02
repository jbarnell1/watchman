namespace Watchman.Service;

public static class Paths
{
    public static string DataDir => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "Watchman");
    public static string ModelsDir => Path.Combine(DataDir, "models");
    public static string HelperExe => Path.Combine(AppContext.BaseDirectory, "WatchmanHelper.exe");
}
