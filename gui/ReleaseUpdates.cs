using System.Text.Json;

namespace Kiln;

/// Opened by an explicit user action; no update checks during conversion/startup.
public sealed class ReleaseUpdates : Form
{
    private readonly KilnInstall _install;
    private readonly IReadOnlyList<string> _clientFolders;
    private readonly string _downloadCache;
    private readonly TextBox _target = new() { Dock = DockStyle.Fill };
    private readonly Label _kiln = new() { Text = "Kiln: not checked", AutoSize = true };
    private readonly Label _engine = new() { Text = "Engine: not checked", AutoSize = true };
    private readonly Label _status = new() { Text = "", AutoSize = true, MaximumSize = new Size(600, 0) };
    private readonly Button _check = new() { Text = "Check again", AutoSize = true };
    private readonly Button _downloadKiln = new() { Text = "Download Kiln ZIP", AutoSize = true, Enabled = false };
    private readonly Button _downloadEngine = new() { Text = "Download engine ZIP", AutoSize = true, Enabled = false };
    private readonly Button _installEngine = new() { Text = "Update installed engine", AutoSize = true, Enabled = false };
    private readonly Button _browse = new() { Text = "Browse...", AutoSize = true };
    private readonly Button _close = new() { Text = "Close", AutoSize = true };
    private readonly Button _cancel = new() { Text = "Cancel request", AutoSize = true, Enabled = false };
    private JsonElement? _releases;
    private CancellationTokenSource? _cts;
    private bool _busy, _applying;

    public ReleaseUpdates(KilnInstall install, string? target, IReadOnlyList<string> clientFolders, string downloadCache)
    {
        _install = install; _clientFolders = clientFolders; _downloadCache = downloadCache;
        Text = "Kiln and engine updates";
        Font = SystemFonts.MessageBoxFont;
        AutoScaleMode = AutoScaleMode.Dpi;
        StartPosition = FormStartPosition.CenterParent;
        MinimumSize = new Size(600, 330); Size = new Size(690, 390);
        var body = new TableLayoutPanel { Dock = DockStyle.Fill, AutoScroll = true, Padding = new Padding(14), ColumnCount = 1 };
        body.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        void Add(Control control) { body.RowStyles.Add(new RowStyle(SizeType.AutoSize)); control.Margin = new Padding(0, 0, 0, 12); body.Controls.Add(control); }
        Add(new Label { Text = "Checks official releases only when requested. Game resources are made locally from your client.", AutoSize = true, MaximumSize = new Size(610, 0) });
        Add(new Label { Text = "Installed game folder (EBOOT.PBP is here):", AutoSize = true });
        var targetRow = new TableLayoutPanel { Dock = DockStyle.Top, AutoSize = true, ColumnCount = 2 };
        targetRow.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100)); targetRow.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        targetRow.Controls.Add(_target, 0, 0); targetRow.Controls.Add(_browse, 1, 0); Add(targetRow);
        Add(_kiln); Add(Flow(_downloadKiln));
        Add(_engine); Add(Flow(_downloadEngine, _installEngine));
        Add(_status); Add(Flow(_check, _cancel, _close));
        Controls.Add(body);
        _target.Text = target ?? "";
        _check.Click += async (_, _) => await CheckAsync();
        _downloadKiln.Click += async (_, _) => await ActAsync(UpdateAction.DownloadKiln);
        _downloadEngine.Click += async (_, _) => await ActAsync(UpdateAction.DownloadEngine);
        _installEngine.Click += async (_, _) => await ActAsync(UpdateAction.InstallEngine);
        _close.Click += (_, _) => Close();
        _cancel.Click += (_, _) => _cts?.Cancel();
        _browse.Click += (_, _) =>
        {
            using var picker = new FolderBrowserDialog { Description = "Choose the installed game folder containing EBOOT.PBP", UseDescriptionForTitle = true, SelectedPath = _target.Text };
            if (picker.ShowDialog(this) == DialogResult.OK) _target.Text = picker.SelectedPath;
        };
        _target.TextChanged += (_, _) => { _releases = null; _engine.Text = "Engine: check again for this folder"; Buttons(); };
        FormClosing += (_, e) => { if (_busy) { e.Cancel = true; if (!_applying) _cts?.Cancel(); } };
        Shown += async (_, _) => await CheckAsync();
    }

    private static FlowLayoutPanel Flow(params Control[] controls)
    {
        var panel = new FlowLayoutPanel { AutoSize = true, Dock = DockStyle.Top, WrapContents = true };
        panel.Controls.AddRange(controls); return panel;
    }

    private void Buttons()
    {
        _check.Enabled = _browse.Enabled = _target.Enabled = _close.Enabled = !_busy;
        _cancel.Enabled = _busy && !_applying;
        _downloadKiln.Enabled = !_busy && _releases?.Obj("kiln")?.Obj("asset") is not null;
        _downloadEngine.Enabled = !_busy && _releases?.Obj("engine")?.Obj("asset") is not null;
        _installEngine.Enabled = !_busy && _target.Text.Trim().Length > 0 &&
            _releases?.Obj("engine")?.Bool("update_available") == true && _releases?.Obj("engine")?.Str("current") is not null;
    }

    private async Task<JsonElement?> RequestAsync(UpdateAction action, string? destination)
    {
        _busy = true; _applying = !UpdateBackend.CanCancel(action);
        using var cts = new CancellationTokenSource(); _cts = cts;
        _status.Text = _applying ? "Downloading and updating the engine. Keep the card connected until this finishes." : action == UpdateAction.Check ? "Checking releases..." : "Downloading...";
        Buttons();
        try { return await UpdateBackend.RunAsync(_install, action, _target.Text.Trim(), destination, cts.Token); }
        catch (OperationCanceledException) { _status.Text = "Request cancelled. You can retry."; }
        catch (Exception e) when (e is IOException or InvalidOperationException or ArgumentException or System.ComponentModel.Win32Exception or UnauthorizedAccessException)
        { _status.Text = e.Message; }
        finally { _cts = null; _busy = _applying = false; Buttons(); }
        return null;
    }

    private async Task CheckAsync()
    {
        _releases = null; Buttons();
        if (await RequestAsync(UpdateAction.Check, null) is not { } result) return;
        _releases = result;
        string Line(string key, string name)
        {
            var row = result.Obj(key);
            return $"{name}: installed {row?.Str("current") ?? "not detected"}; latest {row?.Str("latest") ?? "unavailable"}" +
                (row?.Bool("update_available") == true ? " — update available" : "");
        }
        _kiln.Text = Line("kiln", "Kiln"); _engine.Text = Line("engine", "Engine");
        _status.Text = "Choose a download below. Kiln is a portable ZIP; close the running app before unpacking its new version.";
        Buttons();
    }

    private async Task ActAsync(UpdateAction action)
    {
        if (action == UpdateAction.InstallEngine)
        {
            if (_clientFolders.Any(path => path.Length > 0 && ClientScan.Inside(_downloadCache, path)))
            { _status.Text = "Choose a work folder outside the game client folders in Advanced settings."; return; }
            if (await RequestAsync(action, _downloadCache) is not { } installed) return;
            _status.Text = $"Engine updated. Backup: {installed.Str("backup")}";
            _releases = null; _engine.Text = "Engine updated; Check again to refresh the versions."; Buttons();
            return;
        }
        using var picker = new FolderBrowserDialog { Description = "Choose where to save the release ZIP on this PC", UseDescriptionForTitle = true,
            SelectedPath = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Downloads") };
        if (picker.ShowDialog(this) != DialogResult.OK) return;
        if (_clientFolders.Any(path => path.Length > 0 && ClientScan.Inside(picker.SelectedPath, path)))
        { _status.Text = "Choose a download folder outside the game client folders."; return; }
        if (action == UpdateAction.InstallEngine && MessageBox.Show(this,
            $"Update the engine in {_target.Text}?\n\nKiln saves a backup and preserves your menu artwork, settings and game data. Keep the card connected until the update finishes.",
            "Update engine", MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes) return;
        if (await RequestAsync(action, picker.SelectedPath) is not { } result) return;
        string path = result.Str("path") ?? picker.SelectedPath;
        _status.Text = action == UpdateAction.InstallEngine ? $"Engine updated. Backup: {result.Str("backup")}" :
            $"Saved {path}." + (action == UpdateAction.DownloadKiln ? " Close Kiln, unpack the ZIP into a new folder, then launch it there." : "");
        if (action == UpdateAction.InstallEngine) { _releases = null; _engine.Text = "Engine updated; Check again to refresh the versions."; Buttons(); }
    }
}
