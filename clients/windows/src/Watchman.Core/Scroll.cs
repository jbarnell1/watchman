using System.Drawing;
using System.Drawing.Imaging;

namespace Watchman.Core;

/// <summary>
/// A grayscale copy of a frame at one eighth of its size. Cheap to make (one GDI draw), and enough
/// to fingerprint every pass region and to estimate how far the page scrolled since the last sweep.
/// </summary>
public sealed class GrayThumb
{
    public const int Scale = 8;
    public int Width { get; }
    public int Height { get; }
    public byte[] Pixels { get; }

    public GrayThumb(int width, int height, byte[] pixels)
    {
        Width = width;
        Height = height;
        Pixels = pixels;
    }

    public static GrayThumb From(Bitmap frame)
    {
        var w = Math.Max(1, frame.Width / Scale);
        var h = Math.Max(1, frame.Height / Scale);
        using var small = new Bitmap(w, h, PixelFormat.Format32bppArgb);
        using (var g = Graphics.FromImage(small))
        {
            g.InterpolationMode = System.Drawing.Drawing2D.InterpolationMode.Bilinear;
            g.DrawImage(frame, new Rectangle(0, 0, w, h), new Rectangle(0, 0, frame.Width, frame.Height), GraphicsUnit.Pixel);
        }
        var data = small.LockBits(new Rectangle(0, 0, w, h), ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
        try
        {
            var bgra = new byte[data.Stride * h];
            System.Runtime.InteropServices.Marshal.Copy(data.Scan0, bgra, 0, bgra.Length);
            var gray = new byte[w * h];
            for (var y = 0; y < h; y++)
            for (var x = 0; x < w; x++)
            {
                var p = y * data.Stride + x * 4;
                gray[y * w + x] = (byte)Math.Round(0.299 * bgra[p + 2] + 0.587 * bgra[p + 1] + 0.114 * bgra[p]);
            }
            return new GrayThumb(w, h, gray);
        }
        finally
        {
            small.UnlockBits(data);
        }
    }

    /// <summary>
    /// The dHash of a frame region (frame pixels), read from this thumbnail with the region moved down
    /// by <paramref name="shiftPx"/>. Null when the moved region leaves the frame.
    /// </summary>
    public string? Fingerprint(Rectangle frameRegion, int shiftPx = 0)
    {
        var x0 = frameRegion.X / Scale;
        var y0 = (frameRegion.Y + shiftPx) / Scale;
        var w = Math.Max(1, frameRegion.Width / Scale);
        var h = Math.Max(1, frameRegion.Height / Scale);
        if (y0 < 0 || x0 < 0 || x0 + w > Width || y0 + h > Height) return null;
        var gray = new double[w * h];
        for (var y = 0; y < h; y++)
        for (var x = 0; x < w; x++)
            gray[y * w + x] = Pixels[(y0 + y) * Width + x0 + x];
        return DHash.FromCells(DHash.AreaAverage(gray, w, h));
    }
}

/// <summary>
/// How far a page scrolled between two sweeps (ADR-023 addendum). Compares the two thumbnails at
/// every vertical shift and keeps the shift with the smallest mean difference, if it beats "no
/// shift" clearly. A wrong answer only costs passes: the planner still runs any tile whose content
/// does not match at the estimated shift.
/// </summary>
public static class ScrollEstimator
{
    /// <summary>The shift must cut the error to this share of the unshifted error.</summary>
    public const double MustBeat = 0.6;
    /// <summary>Mean gray difference per pixel above which no shift counts as a match.</summary>
    public const double MaxError = 14.0;

    /// <summary>
    /// Rows the content moved up (positive when the page scrolled down, so the current row y shows
    /// what was at y + dy before), in thumbnail rows, and how clearly it won (0 to 1). (0, 0) when
    /// nothing moved, the sizes differ, or no shift is convincing.
    /// </summary>
    public static (int Dy, double Confidence) Estimate(GrayThumb previous, GrayThumb current, int maxShift)
    {
        if (previous.Width != current.Width || previous.Height != current.Height) return (0, 0);
        var h = current.Height;
        maxShift = Math.Min(maxShift, h * 2 / 3);
        var still = Error(previous, current, 0);
        if (still < 0.5) return (0, 0);
        var best = 0;
        var bestErr = still;
        for (var s = -maxShift; s <= maxShift; s++)
        {
            if (s == 0) continue;
            var e = Error(previous, current, s);
            if (e < bestErr) { bestErr = e; best = s; }
        }
        if (best == 0 || bestErr > MaxError || bestErr > still * MustBeat) return (0, 0);
        return (best, 1 - bestErr / still);
    }

    /// <summary>Mean absolute difference between current row y and previous row y + s over the overlap,
    /// sampling every other row and column (half the cost, the same answer on real screens).</summary>
    private static double Error(GrayThumb prev, GrayThumb cur, int s)
    {
        var w = cur.Width;
        var y0 = Math.Max(0, -s);
        var y1 = Math.Min(cur.Height, prev.Height - s);
        if (y1 - y0 < cur.Height / 3) return double.MaxValue;
        long total = 0;
        long count = 0;
        for (var y = y0; y < y1; y += 2)
        {
            var c = y * w;
            var p = (y + s) * w;
            for (var x = 0; x < w; x += 2)
            {
                total += Math.Abs(cur.Pixels[c + x] - prev.Pixels[p + x]);
                count++;
            }
        }
        return count == 0 ? double.MaxValue : (double)total / count;
    }

    /// <summary>A finding moved with the page: up by <paramref name="shiftPx"/>, or null once it has left the frame.</summary>
    public static Finding? Move(Finding f, int shiftPx, Size frame)
    {
        var r = f.Region;
        r.Offset(0, -shiftPx);
        return r.Bottom <= 0 || r.Top >= frame.Height ? null : f with { Region = Rectangle.Intersect(r, new Rectangle(Point.Empty, frame)) };
    }
}
