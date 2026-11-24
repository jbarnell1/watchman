using Watchman.Service;
using Xunit;

namespace Watchman.Core.Tests;

public class PathsTests
{
    [Fact]
    public void FindsTheHelperInTheInstalledLayout()
    {
        var root = Directory.CreateTempSubdirectory("watchman-paths").FullName;
        try
        {
            var service = Directory.CreateDirectory(Path.Combine(root, "service")).FullName;
            var helper = Directory.CreateDirectory(Path.Combine(root, "helper")).FullName;
            File.WriteAllText(Path.Combine(helper, "WatchmanHelper.exe"), "");
            Assert.Equal(Path.Combine(helper, "WatchmanHelper.exe"), Paths.FindHelper(service + Path.DirectorySeparatorChar));
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void PrefersAHelperBesideTheService()
    {
        var root = Directory.CreateTempSubdirectory("watchman-paths").FullName;
        try
        {
            File.WriteAllText(Path.Combine(root, "WatchmanHelper.exe"), "");
            Assert.Equal(Path.Combine(root, "WatchmanHelper.exe"), Paths.FindHelper(root));
        }
        finally { Directory.Delete(root, true); }
    }
}
