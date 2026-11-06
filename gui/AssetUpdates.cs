using System.Text.Json;

namespace Kiln;

public sealed partial class MainForm
{
    private async Task PlanAssetUpdateAsync()
    {
        if (_busy || !InputsOk() || _inst is null) return;
        if (!_caps.Has("--plan-update")) { await MakeAsync(KilnMode.Update); return; }
        AssetUpdateLocation location;
        try { location = AssetUpdateBackend.Resolve(PcOut(), StickGameDir(), WorkDir()); }
        catch (ArgumentException e) { MessageBox.Show(this, e.Message, "Update assets"); return; }
        var options = Options(KilnMode.Update);
        options.Out = location.Out; options.Target = location.Target;
        options.Server = null; options.Eboot = null; options.Force = false;
        JsonElement? plan = null;
        BeginBusy("check");
        try
        {
            var process = new KilnProcess();
            process.Event += (kind, doc) =>
            {
                if (kind == "plan") plan = doc.Clone();
                else if (kind == "error") BeginInvoke(() => Log(doc.Str("message") ?? doc.ToString()));
            };
            process.Text += text => BeginInvoke(() => Log(text));
            int rc = await process.RunAsync(_inst, AssetUpdateBackend.PlanArgs(options, _inst, _caps), _runCts!.Token);
            if (_cancelled) return;
            if (rc != 0 || plan is null)
            {
                MessageBox.Show(this, "The resource update plan could not be made. See the Log tab for the reason.", "Update assets");
                return;
            }
        }
        catch (Exception e) when (e is IOException or InvalidOperationException or System.ComponentModel.Win32Exception)
        { MessageBox.Show(this, e.Message, "Update assets"); return; }
        finally { EndBusy(); }

        var rows = AssetUpdateBackend.Rows(plan.Value);
        using var selection = new AssetUpdates(rows, location.Out, location.OnCard);
        if (selection.ShowDialog(this) != DialogResult.OK) return;
        options.Steps = selection.SelectedSteps.ToList();
        if (options.Steps.Count == 0) return;
        SaveSettings();
        BeginBusy("update");
        int code = -1;
        try { code = await RunKilnAsync(options); }
        catch (Exception e) when (e is IOException or InvalidOperationException or System.ComponentModel.Win32Exception)
        { Log(e.Message); }
        finally { EndBusy(); }
        string title = _cancelled ? "Resource update cancelled; completed packs are kept." :
            code == 0 ? "Selected resources updated." : "Resource update finished with problems. See the Log tab.";
        var lines = new List<string>
        {
            $"Updated installation: {location.Out}",
            $"Selected packs: {string.Join(", ", options.Steps)}",
            "Other packs and installed map regions were kept. No game resources were downloaded.",
            $"Temporary client files: {WorkDir()}",
        };
        if (_lastDone is { } done)
            lines.Add($"Steps: {done.Num("made") ?? 0} made, {done.Num("skipped") ?? 0} already current.");
        if (location.OnCard) lines.Add("The selected installation was updated directly; a full PC copy was not needed.");
        SetSummary(title, code == 0 && !_cancelled ? Good : Warn, lines, null);
        if (location.OnCard) _sumOpenOut.Enabled = false;
        SetStatus(title, code == 0 && !_cancelled ? Good : Warn);
        _tabs.SelectedIndex = 2;
    }
}

public sealed class AssetUpdates : Form
{
    private readonly ListView _rows = new() { Dock = DockStyle.Fill, View = View.Details, CheckBoxes = true,
        FullRowSelect = true, HideSelection = false, GridLines = true };
    public IEnumerable<string> SelectedSteps => _rows.CheckedItems.Cast<ListViewItem>().Select(item => (string)item.Tag!);

    public AssetUpdates(IReadOnlyList<AssetUpdateRow> rows, string output, bool onCard)
    {
        Text = "Choose resources to update"; StartPosition = FormStartPosition.CenterParent;
        AutoScaleMode = AutoScaleMode.Dpi; Font = SystemFonts.MessageBoxFont;
        MinimumSize = new Size(650, 360); Size = new Size(850, 530);
        var body = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(12), ColumnCount = 1, RowCount = 3 };
        body.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        body.RowStyles.Add(new RowStyle(SizeType.AutoSize)); body.RowStyles.Add(new RowStyle(SizeType.Percent, 100)); body.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        var info = new Label { AutoSize = true, Dock = DockStyle.Top, MaximumSize = new Size(790, 0), Margin = new Padding(0, 0, 0, 10),
            Text = $"{output}\nChanged or missing packs are selected. Only selected packs are processed; each pack may need a full rebuild." +
                   (onCard ? "\nThis installation is on the memory stick. Temporary files stay in the PC work folder; keep the card connected." : "") };
        body.Controls.Add(info, 0, 0);
        _rows.Columns.Add("Pack", 200); _rows.Columns.Add("Resources", 270); _rows.Columns.Add("Why", 300);
        foreach (var row in rows)
        {
            var item = new ListViewItem(row.Name) { Tag = row.Name, Checked = row.Selected };
            item.SubItems.Add(row.Title); item.SubItems.Add(row.Reason);
            _rows.Items.Add(item);
        }
        body.Controls.Add(_rows, 0, 1);
        var buttons = new FlowLayoutPanel { Dock = DockStyle.Top, AutoSize = true, WrapContents = true };
        var changed = new Button { Text = "Select changed", AutoSize = true };
        var none = new Button { Text = "Clear selection", AutoSize = true };
        var update = new Button { Text = "Update selected", AutoSize = true };
        var cancel = new Button { Text = "Cancel", AutoSize = true, DialogResult = DialogResult.Cancel };
        changed.Click += (_, _) => { for (int i = 0; i < rows.Count; i++) _rows.Items[i].Checked = rows[i].Selected; };
        none.Click += (_, _) => { foreach (ListViewItem item in _rows.Items) item.Checked = false; };
        update.Click += (_, _) =>
        {
            if (!SelectedSteps.Any()) { MessageBox.Show(this, "Choose at least one pack, or Cancel.", "Update assets"); return; }
            DialogResult = DialogResult.OK; Close();
        };
        buttons.Controls.AddRange(new Control[] { changed, none, update, cancel });
        body.Controls.Add(buttons, 0, 2); Controls.Add(body);
        AcceptButton = update; CancelButton = cancel;
    }
}
