using System.Drawing;
using System.Drawing.Drawing2D;

namespace Watchman.Helper;

/// <summary>One per monitor: a topmost, click-through, layered window that paints a heavy pixelation
/// over each covered region and a full block when most of the screen is explicit. Excluded from
/// screen capture so the next frame still shows what is underneath (ADR-003).</summary>
public sealed class OverlayForm : Form
{
    private readonly List<(Rectangle Region, Bitmap Patch, DateTime Until)> _patches = new();
    private bool _blocked;
    private static readonly Color Key = Color.FromArgb(1, 2, 3);

    public OverlayForm(Rectangle bounds)
    {
        FormBorderStyle = FormBorderStyle.None;
        ShowInTaskbar = false;
        TopMost = true;
        StartPosition = FormStartPosition.Manual;
        Bounds = bounds;
        BackColor = Key;
        TransparencyKey = Key;
        DoubleBuffered = true;
    }

    protected override CreateParams CreateParams
    {
        get
        {
            var cp = base.CreateParams;
            cp.ExStyle |= Native.WS_EX_TRANSPARENT | Native.WS_EX_LAYERED | Native.WS_EX_TOOLWINDOW | Native.WS_EX_NOACTIVATE | Native.WS_EX_TOPMOST;
            return cp;
        }
    }

    protected override bool ShowWithoutActivation => true;

    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        Native.SetWindowDisplayAffinity(Handle, Native.WDA_EXCLUDEFROMCAPTURE);
    }

    /// <summary>Replace the patches: each region (in this screen's coordinates) gets a pixelated copy of
    /// the crop so colour and layout stay while detail goes. Patches outlive the pass by `hold`.</summary>
    public void SetPatches(IEnumerable<(Rectangle Region, Bitmap Crop)> found, TimeSpan hold, bool blocked)
    {
        var now = DateTime.UtcNow;
        _patches.RemoveAll(p => { var gone = p.Until < now; if (gone) p.Patch.Dispose(); return gone; });
        foreach (var (region, crop) in found)
        {
            var existing = _patches.FindIndex(p => p.Region == region);
            var patch = Pixelate(crop, region.Size);
            if (existing >= 0) { _patches[existing].Patch.Dispose(); _patches[existing] = (region, patch, now + hold); }
            else _patches.Add((region, patch, now + hold));
        }
        _blocked = blocked;
        if (!Visible && (_patches.Count > 0 || blocked)) Show();
        Invalidate();
    }

    /// <summary>The page scrolled: move every patch up by <paramref name="shiftPx"/> and drop the ones that left.</summary>
    public void Shift(int shiftPx)
    {
        if (shiftPx == 0 || _patches.Count == 0) return;
        var area = new Rectangle(Point.Empty, ClientSize);
        for (var i = _patches.Count - 1; i >= 0; i--)
        {
            var (region, patch, until) = _patches[i];
            region.Offset(0, -shiftPx);
            if (!region.IntersectsWith(area)) { patch.Dispose(); _patches.RemoveAt(i); continue; }
            _patches[i] = (region, patch, until);
        }
        Invalidate();
    }

    public bool HasPatches => _patches.Count > 0 || _blocked;

    public static Bitmap Pixelate(Bitmap crop, Size size)
    {
        var tinyW = Math.Max(2, size.Width / 24);
        var tinyH = Math.Max(2, size.Height / 24);
        using var tiny = new Bitmap(crop, tinyW, tinyH);
        var patch = new Bitmap(size.Width, size.Height);
        using var g = Graphics.FromImage(patch);
        g.InterpolationMode = InterpolationMode.NearestNeighbor;
        g.PixelOffsetMode = PixelOffsetMode.Half;
        g.DrawImage(tiny, new Rectangle(0, 0, size.Width, size.Height));
        return patch;
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        e.Graphics.Clear(Key);
        if (_blocked)
        {
            e.Graphics.Clear(Color.FromArgb(11, 17, 20));
            using var font = new Font("Segoe UI", 22, FontStyle.Regular);
            using var brush = new SolidBrush(Color.FromArgb(242, 245, 246));
            var text = "Covered by Watchman";
            var size = e.Graphics.MeasureString(text, font);
            e.Graphics.DrawString(text, font, brush, (Width - size.Width) / 2, (Height - size.Height) / 2);
            return;
        }
        foreach (var (region, patch, _) in _patches) e.Graphics.DrawImageUnscaled(patch, region.Location);
    }
}

/// <summary>Owns one OverlayForm per screen and keeps them matched to the monitors.</summary>
public sealed class Overlays : IDisposable
{
    private readonly Dictionary<Rectangle, OverlayForm> _forms = new();

    public OverlayForm For(Rectangle bounds)
    {
        if (!_forms.TryGetValue(bounds, out var form))
        {
            form = new OverlayForm(bounds);
            _forms[bounds] = form;
        }
        return form;
    }

    public bool AnyVisible => _forms.Values.Any(f => f.Visible);

    public void Dispose()
    {
        foreach (var f in _forms.Values) f.Dispose();
        _forms.Clear();
    }
}
