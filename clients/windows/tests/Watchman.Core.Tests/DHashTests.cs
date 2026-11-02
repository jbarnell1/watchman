using System.Drawing;
using System.Text.Json;
using Watchman.Core;
using Xunit;

namespace Watchman.Core.Tests;

public class DHashTests
{
    private static string Vectors
    {
        get
        {
            var dir = AppContext.BaseDirectory;
            while (dir is not null && !Directory.Exists(Path.Combine(dir, "models", "test-vectors")))
                dir = Path.GetDirectoryName(dir);
            return Path.Combine(dir!, "models", "test-vectors", "dhash");
        }
    }

    [Fact]
    public void ReproducesEveryCommittedVector()
    {
        using var doc = JsonDocument.Parse(File.ReadAllText(Path.Combine(Vectors, "vectors.json")));
        var vectors = doc.RootElement.GetProperty("vectors").EnumerateArray().ToList();
        Assert.True(vectors.Count >= 8);
        foreach (var v in vectors)
        {
            using var bmp = new Bitmap(Path.Combine(Vectors, v.GetProperty("file").GetString()!));
            Assert.Equal(v.GetProperty("width").GetInt32(), bmp.Width);
            Assert.Equal(v.GetProperty("dhash").GetString(), DHash.Compute(bmp));
        }
    }

    [Fact]
    public void FlatImageHashesToZero()
    {
        using var bmp = new Bitmap(20, 20);
        using (var g = Graphics.FromImage(bmp)) g.Clear(Color.FromArgb(77, 77, 77));
        Assert.Equal("0000000000000000", DHash.Compute(bmp));
    }

    [Fact]
    public void HammingAndTolerance()
    {
        Assert.Equal(4, DHash.Hamming("0000000000000000", "000000000000000f"));
        Assert.True(DHash.Matches("0000000000000000", "000000000000000f"));
        Assert.False(DHash.Matches("0000000000000000", "00000000000000ff"));
    }
}
