using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;

namespace Watchman.Core;

public sealed record Letterbox(double Scale, int PadX, int PadY);

public sealed record Box(string Label, double Score, double X, double Y, double W, double H)
{
    public Rectangle ToRectangle() => Rectangle.Round(new RectangleF((float)X, (float)Y, (float)W, (float)H));
}

/// <summary>Manifest-driven preprocessing and output decoding, matching models/watchman_models.</summary>
public static class Vision
{
    public const int Pad = 114;

    /// <summary>Letterbox a bitmap into size by size (pad 114) and return the NCHW float tensor.</summary>
    public static (float[] Tensor, Letterbox Box) LetterboxTensor(Bitmap image, ModelInput input)
    {
        var size = input.Size;
        var scale = Math.Min((double)size / image.Width, (double)size / image.Height);
        var nw = Math.Max(1, (int)Math.Round(image.Width * scale));
        var nh = Math.Max(1, (int)Math.Round(image.Height * scale));
        var padX = (size - nw) / 2;
        var padY = (size - nh) / 2;
        using var canvas = new Bitmap(size, size, PixelFormat.Format32bppArgb);
        using (var g = Graphics.FromImage(canvas))
        {
            g.Clear(Color.FromArgb(Pad, Pad, Pad));
            g.InterpolationMode = InterpolationMode.Bilinear;
            g.PixelOffsetMode = PixelOffsetMode.Half;
            g.DrawImage(image, new Rectangle(padX, padY, nw, nh));
        }
        return (ToTensor(canvas, input), new Letterbox(scale, padX, padY));
    }

    /// <summary>Resize the whole image to size by size and return the tensor (classifier entries).</summary>
    public static float[] ClassifierTensor(Bitmap image, ModelInput input)
    {
        using var canvas = new Bitmap(input.Size, input.Size, PixelFormat.Format32bppArgb);
        using (var g = Graphics.FromImage(canvas))
        {
            g.InterpolationMode = input.Resize == "bicubic" ? InterpolationMode.HighQualityBicubic : InterpolationMode.HighQualityBilinear;
            g.PixelOffsetMode = PixelOffsetMode.Half;
            g.DrawImage(image, new Rectangle(0, 0, input.Size, input.Size));
        }
        return ToTensor(canvas, input);
    }

    public static float[] ToTensor(Bitmap square, ModelInput input)
    {
        var size = square.Width;
        var n = size * size;
        var data = square.LockBits(new Rectangle(0, 0, size, size), ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
        try
        {
            var bytes = new byte[data.Stride * size];
            System.Runtime.InteropServices.Marshal.Copy(data.Scan0, bytes, 0, bytes.Length);
            var outp = new float[3 * n];
            var order = input.Color == "BGR" ? new[] { 0, 1, 2 } : new[] { 2, 1, 0 }; // memory is BGRA
            for (var y = 0; y < size; y++)
            {
                for (var x = 0; x < size; x++)
                {
                    var p = y * data.Stride + x * 4;
                    var i = y * size + x;
                    for (var c = 0; c < 3; c++)
                    {
                        var v = (float)((bytes[p + order[c]] / input.Scale - input.Mean[c]) / input.Std[c]);
                        if (input.Layout == "NCHW") outp[c * n + i] = v;
                        else outp[i * 3 + c] = v;
                    }
                }
            }
            return outp;
        }
        finally
        {
            square.UnlockBits(data);
        }
    }

    public static double Softmax(float[] logits, ModelOutput output)
    {
        var idx = output.Labels.IndexOf(output.Positive ?? output.Labels[0]);
        if (output.Kind == "sigmoid") return 1 / (1 + Math.Exp(-logits[idx]));
        var max = logits.Max();
        var exps = logits.Select(v => Math.Exp(v - max)).ToArray();
        return exps[idx] / exps.Sum();
    }

    /// <summary>Decode Ultralytics YOLOv8 output (1, 4+classes, anchors): boxes in original pixels after NMS.</summary>
    public static List<Box> DecodeYolo(float[] raw, int[] dims, ModelOutput output, Letterbox? lb, double conf = 0.25, double nmsIou = 0.45)
    {
        var classes = output.Labels.Count;
        int rows, anchors;
        bool transposed;
        if (dims.Length == 3 && dims[1] == 4 + classes) { rows = dims[1]; anchors = dims[2]; transposed = false; }
        else if (dims.Length == 3 && dims[2] == 4 + classes) { rows = dims[2]; anchors = dims[1]; transposed = true; }
        else throw new ArgumentException($"unexpected output shape {string.Join('x', dims)}");

        double At(int r, int a) => transposed ? raw[a * rows + r] : raw[r * anchors + a];

        var boxes = new List<Box>();
        for (var a = 0; a < anchors; a++)
        {
            var best = 0;
            var bestScore = double.NegativeInfinity;
            for (var c = 0; c < classes; c++)
            {
                var s = At(4 + c, a);
                if (s > bestScore) { bestScore = s; best = c; }
            }
            if (bestScore < conf) continue;
            double cx = At(0, a), cy = At(1, a), w = At(2, a), h = At(3, a);
            double x = cx - w / 2, y = cy - h / 2;
            if (lb is not null)
            {
                x = (x - lb.PadX) / lb.Scale; y = (y - lb.PadY) / lb.Scale; w /= lb.Scale; h /= lb.Scale;
            }
            boxes.Add(new Box(output.Labels[best], bestScore, x, y, w, h));
        }
        var kept = new List<Box>();
        foreach (var label in boxes.Select(b => b.Label).Distinct())
        {
            var same = boxes.Where(b => b.Label == label).OrderByDescending(b => b.Score).ToList();
            while (same.Count > 0)
            {
                var top = same[0];
                same.RemoveAt(0);
                kept.Add(top);
                same = same.Where(b => Iou(top, b) < nmsIou).ToList();
            }
        }
        return kept.OrderByDescending(b => b.Score).ToList();
    }

    public static double Iou(Box a, Box b)
    {
        var iw = Math.Max(0, Math.Min(a.X + a.W, b.X + b.W) - Math.Max(a.X, b.X));
        var ih = Math.Max(0, Math.Min(a.Y + a.H, b.Y + b.H) - Math.Max(a.Y, b.Y));
        var inter = iw * ih;
        var union = a.W * a.H + b.W * b.H - inter;
        return union > 0 ? inter / union : 0;
    }

    public static double ExplicitScore(IEnumerable<Box> boxes, ModelOutput output)
    {
        var explicitLabels = new HashSet<string>(output.ExplicitLabels ?? new List<string>());
        return boxes.Where(b => explicitLabels.Contains(b.Label)).Select(b => b.Score).DefaultIfEmpty(0).Max();
    }

    /// <summary>Tiles of size by size over a frame, overlapping by a quarter so a region on a seam is seen whole.</summary>
    public static List<Rectangle> Tiles(int width, int height, int size)
    {
        var tiles = new List<Rectangle>();
        if (width <= size && height <= size) { tiles.Add(new Rectangle(0, 0, width, height)); return tiles; }
        var step = size * 3 / 4;
        for (var y = 0; y < height; y += step)
        {
            for (var x = 0; x < width; x += step)
            {
                var r = new Rectangle(Math.Min(x, Math.Max(0, width - size)), Math.Min(y, Math.Max(0, height - size)),
                    Math.Min(size, width), Math.Min(size, height));
                if (!tiles.Contains(r)) tiles.Add(r);
                if (x + size >= width) break;
            }
            if (y + size >= height) break;
        }
        return tiles;
    }

    public static Bitmap Crop(Bitmap source, Rectangle region)
    {
        var r = Rectangle.Intersect(region, new Rectangle(0, 0, source.Width, source.Height));
        if (r.Width < 1 || r.Height < 1) r = new Rectangle(0, 0, 1, 1);
        return source.Clone(r, PixelFormat.Format32bppArgb);
    }

    public static byte[] ToJpeg(Bitmap image, int maxSide = 512, long quality = 85)
    {
        var scale = Math.Min(1.0, (double)maxSide / Math.Max(image.Width, image.Height));
        using var scaled = scale < 1 ? new Bitmap(image, (int)Math.Round(image.Width * scale), (int)Math.Round(image.Height * scale)) : new Bitmap(image);
        var codec = ImageCodecInfo.GetImageEncoders().First(c => c.FormatID == ImageFormat.Jpeg.Guid);
        using var p = new EncoderParameters(1);
        p.Param[0] = new EncoderParameter(Encoder.Quality, quality);
        using var ms = new MemoryStream();
        scaled.Save(ms, codec, p);
        return ms.ToArray();
    }
}
