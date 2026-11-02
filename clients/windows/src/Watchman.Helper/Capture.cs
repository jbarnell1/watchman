using System.Drawing;
using System.Drawing.Imaging;

namespace Watchman.Helper;

public sealed record ScreenFrame(int Index, Rectangle Bounds, Bitmap Image) : IDisposable
{
    public void Dispose() => Image.Dispose();
}

/// <summary>Screen capture behind an interface so the watcher can be driven with synthetic frames.</summary>
public interface IScreenCapture
{
    IReadOnlyList<Rectangle> Screens { get; }
    ScreenFrame Capture(int index);
}

/// <summary>GDI capture of each monitor. The overlay is excluded from capture by the window display
/// affinity, so Watchman never sees its own patches.</summary>
public sealed class GdiCapture : IScreenCapture
{
    public IReadOnlyList<Rectangle> Screens => Screen.AllScreens.Select(s => s.Bounds).ToList();

    public ScreenFrame Capture(int index)
    {
        var bounds = Screen.AllScreens[index].Bounds;
        var bmp = new Bitmap(bounds.Width, bounds.Height, PixelFormat.Format32bppArgb);
        using (var g = Graphics.FromImage(bmp))
        {
            g.CopyFromScreen(bounds.Left, bounds.Top, 0, 0, bounds.Size, CopyPixelOperation.SourceCopy);
        }
        return new ScreenFrame(index, bounds, bmp);
    }
}
