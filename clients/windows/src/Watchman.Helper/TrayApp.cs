using System.Drawing;
using Watchman.Core;

namespace Watchman.Helper;

/// <summary>The tray icon: status, a partner code to pause, a request for one. No way to stop
/// protection from here; closing the helper only makes the service start it again.</summary>
public sealed class TrayApp : ApplicationContext
{
    private readonly NotifyIcon _icon;
    private readonly PipeClient _pipe = new();
    private readonly Overlays _overlays = new();
    private readonly Watcher _watcher;
    private readonly ToolStripMenuItem _status = new("Starting") { Enabled = false };

    public TrayApp()
    {
        _watcher = new Watcher(new GdiCapture(), _overlays, _pipe);
        var menu = new ContextMenuStrip();
        menu.Items.Add(_status);
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("Enter a partner code", null, (_, _) => AskCode());
        menu.Items.Add("Ask my partners for a pause", null, (_, _) => AskPause());
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("About Watchman", null, (_, _) => MessageBox.Show(
            "Watchman judges what is on this screen on this computer and covers explicit images. " +
            "It reports to your household's own server. Nothing pauses it without a partner code.",
            "Watchman", MessageBoxButtons.OK, MessageBoxIcon.None));
        _icon = new NotifyIcon
        {
            Icon = LoadIcon(), Text = "Watchman", Visible = true, ContextMenuStrip = menu,
        };
        _watcher.StatusChanged += () =>
        {
            _status.Text = _watcher.Status;
            var text = "Watchman: " + _watcher.Status + (_watcher.Detail.Length > 0 ? " (" + _watcher.Detail + ")" : "");
            _icon.Text = text.Length > 63 ? text[..63] : text;
        };
        _pipe.ResultReceived += r => _icon.ShowBalloonTip(4000, "Watchman", r.Message ?? (r.Ok ? "done" : "failed"), r.Ok ? ToolTipIcon.None : ToolTipIcon.Warning);
        _pipe.Start();
    }

    private static Icon LoadIcon()
    {
        var path = Path.Combine(AppContext.BaseDirectory, "mark.ico");
        return File.Exists(path) ? new Icon(path) : SystemIcons.Shield;
    }

    private void AskCode()
    {
        var code = Prompt("Partner code", "The code your partner gave you:");
        if (!string.IsNullOrWhiteSpace(code)) _ = _pipe.SendAsync(new UnlockMessage(code.Trim()));
    }

    private void AskPause()
    {
        var minutes = Prompt("Ask for a pause", "How many minutes?", "30");
        if (!int.TryParse(minutes, out var m) || m < 1) return;
        var reason = Prompt("Ask for a pause", "Why? (your partners see this)") ?? "";
        _ = _pipe.SendAsync(new RequestUnlockMessage(m, reason));
    }

    private static string? Prompt(string title, string label, string initial = "")
    {
        using var form = new Form { Text = title, Width = 380, Height = 170, FormBorderStyle = FormBorderStyle.FixedDialog,
            StartPosition = FormStartPosition.CenterScreen, MinimizeBox = false, MaximizeBox = false, TopMost = true };
        var lbl = new Label { Left = 16, Top = 16, Width = 340, Text = label };
        var box = new TextBox { Left = 16, Top = 44, Width = 340, Text = initial };
        var ok = new Button { Text = "OK", Left = 196, Top = 84, Width = 76, DialogResult = DialogResult.OK };
        var cancel = new Button { Text = "Cancel", Left = 280, Top = 84, Width = 76, DialogResult = DialogResult.Cancel };
        form.Controls.AddRange(new Control[] { lbl, box, ok, cancel });
        form.AcceptButton = ok;
        form.CancelButton = cancel;
        return form.ShowDialog() == DialogResult.OK ? box.Text : null;
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            _icon.Visible = false;
            _icon.Dispose();
            _watcher.Dispose();
            _overlays.Dispose();
            _pipe.Dispose();
        }
        base.Dispose(disposing);
    }
}
