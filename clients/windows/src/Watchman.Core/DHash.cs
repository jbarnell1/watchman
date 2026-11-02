using System.Drawing;
using System.Drawing.Imaging;

namespace Watchman.Core;

/// <summary>
/// The image hash from docs/api-contract.md: grayscale, area-average to 9 by 8, round to integers,
/// one bit per horizontal neighbour pair. Must reproduce models/test-vectors/dhash/vectors.json.
/// </summary>
public static class DHash
{
    private const int W = 9;
    private const int H = 8;

    /// <summary>pixels are 32-bit BGRA rows (System.Drawing's Format32bppArgb in memory order).</summary>
    public static string Compute(ReadOnlySpan<byte> bgra, int width, int height, int stride)
    {
        var gray = new double[width * height];
        for (var y = 0; y < height; y++)
        {
            var row = y * stride;
            for (var x = 0; x < width; x++)
            {
                var p = row + x * 4;
                gray[y * width + x] = 0.299 * bgra[p + 2] + 0.587 * bgra[p + 1] + 0.114 * bgra[p];
            }
        }
        return FromCells(AreaAverage(gray, width, height));
    }

    public static string Compute(Bitmap bitmap)
    {
        using var copy = bitmap.PixelFormat == PixelFormat.Format32bppArgb ? null : bitmap.Clone(new Rectangle(0, 0, bitmap.Width, bitmap.Height), PixelFormat.Format32bppArgb);
        var src = copy ?? bitmap;
        var data = src.LockBits(new Rectangle(0, 0, src.Width, src.Height), ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
        try
        {
            var bytes = new byte[data.Stride * data.Height];
            System.Runtime.InteropServices.Marshal.Copy(data.Scan0, bytes, 0, bytes.Length);
            return Compute(bytes, src.Width, src.Height, data.Stride);
        }
        finally
        {
            src.UnlockBits(data);
        }
    }

    /// <summary>
    /// The hash of a region through a 72 by 64 thumbnail: a fingerprint for change detection between
    /// sweeps (ADR-023), cheap on a whole 1440p frame. Not the contract hash of a picture.
    /// </summary>
    public static string Fingerprint(Bitmap frame, Rectangle region)
    {
        using var thumb = new Bitmap(72, 64, PixelFormat.Format32bppArgb);
        using (var g = Graphics.FromImage(thumb))
        {
            g.InterpolationMode = System.Drawing.Drawing2D.InterpolationMode.HighQualityBilinear;
            g.DrawImage(frame, new Rectangle(0, 0, 72, 64), region, GraphicsUnit.Pixel);
        }
        return Compute(thumb);
    }

    public static double[] AreaAverage(double[] gray, int inW, int inH)
    {
        var outp = new double[W * H];
        for (var oy = 0; oy < H; oy++)
        {
            var y0 = oy * (double)inH / H;
            var y1 = (oy + 1) * (double)inH / H;
            for (var ox = 0; ox < W; ox++)
            {
                var x0 = ox * (double)inW / W;
                var x1 = (ox + 1) * (double)inW / W;
                double total = 0, area = 0;
                for (var y = (int)Math.Floor(y0); y < Math.Min(inH, (int)Math.Ceiling(y1)); y++)
                {
                    var cy = Math.Min(y + 1, y1) - Math.Max(y, y0);
                    if (cy <= 0) continue;
                    for (var x = (int)Math.Floor(x0); x < Math.Min(inW, (int)Math.Ceiling(x1)); x++)
                    {
                        var cx = Math.Min(x + 1, x1) - Math.Max(x, x0);
                        if (cx <= 0) continue;
                        total += gray[y * inW + x] * cx * cy;
                        area += cx * cy;
                    }
                }
                outp[oy * W + ox] = total / area;
            }
        }
        return outp;
    }

    public static string FromCells(double[] small)
    {
        var cells = new int[small.Length];
        for (var i = 0; i < small.Length; i++) cells[i] = (int)Math.Floor(small[i] + 0.5);
        ulong bits = 0;
        for (var row = 0; row < H; row++)
            for (var col = 0; col < W - 1; col++)
                if (cells[row * W + col] > cells[row * W + col + 1])
                    bits |= 1UL << (63 - (row * 8 + col));
        return bits.ToString("x16");
    }

    public static int Hamming(string a, string b) =>
        System.Numerics.BitOperations.PopCount(Convert.ToUInt64(a, 16) ^ Convert.ToUInt64(b, 16));

    public static bool Matches(string a, string b, int tolerance = 6) => Hamming(a, b) <= tolerance;
}
