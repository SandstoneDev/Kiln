// The step table of a run: one row per step of the plan (and one per extraction / PNG phase), with
// its state, a progress bar (elapsed time against the step's last duration), time and output size.
namespace Kiln;

public enum RowState { Planned, UpToDate, Queued, Running, Done, Skipped, Failed, Cancelled, Missing }

public sealed class StepRow
{
    public string Name = "";
    public string Group = "";
    public string Locale = "";
    public string Title = "";
    public RowState State;
    public double Weight;              // expected seconds of one job
    public bool HistoryWeight;         // Weight came from an earlier run (manifest or this PC)
    public DateTime? Started;
    public double Seconds;
    public int Files;
    public long Bytes;
    public string Note = "";
    public bool Phase;                 // extraction / PNG pass, not a step of the table
    public ListViewItem? Item;

    public bool Finished => State is RowState.Done or RowState.Skipped or RowState.UpToDate or RowState.Failed;

    public double Elapsed => State == RowState.Running && Started is { } s ? (DateTime.UtcNow - s).TotalSeconds : Seconds;

    /// 0..1: finished = 1, running = elapsed against the expected time (held below 1 until it ends).
    public double Fraction => State switch
    {
        RowState.Done or RowState.Skipped or RowState.UpToDate or RowState.Failed => 1.0,
        RowState.Running => Weight > 0 ? Math.Min(0.95, Elapsed / Weight) : 0.5,
        _ => 0.0,
    };
}

public sealed class StepList : ListView
{
    private readonly Dictionary<string, StepRow> _rows = new(StringComparer.Ordinal);
    private const int ColProgress = 4;

    public StepList()
    {
        View = View.Details;
        FullRowSelect = true;
        OwnerDraw = true;
        HeaderStyle = ColumnHeaderStyle.Nonclickable;
        HideSelection = false;
        MultiSelect = false;
        DoubleBuffered = true;
        Columns.Add("step");
        Columns.Add("group");
        Columns.Add("language");
        Columns.Add("state");
        Columns.Add("progress");
        Columns.Add("time", 80, HorizontalAlignment.Right);
        Columns.Add("output", 110, HorizontalAlignment.Right);
        Columns.Add("what it makes");
        ScaleColumns(1f);
        DrawColumnHeader += (_, e) => e.DrawDefault = true;
        DrawItem += (_, e) => { };
        DrawSubItem += OnDrawSubItem;
    }

    private static readonly int[] BaseWidths = { 170, 100, 84, 90, 120, 72, 110, 460 };

    /// Column widths for a scale factor (DPI / 96): ListView columns do not follow the DPI by themselves.
    public void ScaleColumns(float scale)
    {
        for (int i = 0; i < Columns.Count && i < BaseWidths.Length; i++)
            Columns[i].Width = (int)Math.Round(BaseWidths[i] * scale);
    }

    public IReadOnlyCollection<StepRow> Rows => _rows.Values;

    public StepRow? Get(string name) => _rows.TryGetValue(name, out var r) ? r : null;

    public void ClearRows()
    {
        BeginUpdate();
        Items.Clear();
        _rows.Clear();
        EndUpdate();
    }

    public StepRow Upsert(StepRow r, bool atTop = false)
    {
        if (_rows.TryGetValue(r.Name, out var old))
        {
            old.Group = r.Group; old.Locale = r.Locale; old.Title = r.Title;
            if (!old.Finished && old.State != RowState.Running) { old.State = r.State; old.Weight = r.Weight; old.HistoryWeight = r.HistoryWeight; }
            PaintRow(old);
            return old;
        }
        _rows[r.Name] = r;
        r.Item = new ListViewItem(new string[Columns.Count]) { Tag = r };
        if (atTop)
        {
            int at = 0;
            while (at < Items.Count && Items[at].Tag is StepRow { Phase: true }) at++;
            Items.Insert(at, r.Item);
        }
        else Items.Add(r.Item);
        PaintRow(r);
        return r;
    }

    public void PaintRow(StepRow r)
    {
        if (r.Item is null) return;
        var s = r.Item.SubItems;
        s[0].Text = r.Name;
        s[1].Text = r.Group;
        s[2].Text = r.Locale;
        s[3].Text = StateText(r.State);
        s[4].Text = "";
        double t = r.Elapsed;
        s[5].Text = r.State == RowState.Running || t > 0 ? Human(t) : r.Weight > 0 && r.HistoryWeight ? "~" + Human(r.Weight) : "";
        s[6].Text = r.Files > 0 ? $"{r.Files} / {r.Bytes / 1048576.0:0.0} MB" : "";
        s[7].Text = r.Note.Length > 0 ? r.Title + "  (" + r.Note + ")" : r.Title;
        r.Item.ForeColor = r.State switch
        {
            RowState.Failed or RowState.Missing => Color.Firebrick,
            RowState.UpToDate or RowState.Skipped => SystemColors.GrayText,
            RowState.Done => Color.DarkGreen,
            RowState.Running => Color.RoyalBlue,
            _ => SystemColors.WindowText,
        };
        Invalidate(r.Item.Bounds);
    }

    public void PaintRunning()
    {
        BeginUpdate();
        foreach (var r in _rows.Values)
            if (r.State == RowState.Running) PaintRow(r);
        EndUpdate();
    }

    public void EnsureVisibleRow(StepRow r)
    {
        if (r.Item is not null && r.Item.Index >= 0) r.Item.EnsureVisible();
    }

    private void OnDrawSubItem(object? sender, DrawListViewSubItemEventArgs e)
    {
        if (e.ColumnIndex != ColProgress || e.Item?.Tag is not StepRow r)
        {
            e.DrawDefault = true;
            return;
        }
        e.DrawBackground();
        var b = e.Bounds;
        var bar = new Rectangle(b.X + 4, b.Y + 4, Math.Max(0, b.Width - 8), Math.Max(0, b.Height - 8));
        if (bar.Width <= 0 || bar.Height <= 0) return;
        using (var back = new SolidBrush(Color.FromArgb(232, 232, 232))) e.Graphics.FillRectangle(back, bar);
        double f = r.Fraction;
        if (f > 0)
        {
            var fill = new Rectangle(bar.X, bar.Y, (int)(bar.Width * f), bar.Height);
            var c = r.State switch
            {
                RowState.Failed => Color.FromArgb(214, 96, 96),
                RowState.UpToDate or RowState.Skipped => Color.FromArgb(176, 196, 176),
                RowState.Running => Color.FromArgb(96, 150, 220),
                _ => Color.FromArgb(96, 176, 112),
            };
            using var br = new SolidBrush(c);
            e.Graphics.FillRectangle(br, fill);
        }
        using var pen = new Pen(Color.FromArgb(200, 200, 200));
        e.Graphics.DrawRectangle(pen, bar);
    }

    public static string StateText(RowState s) => s switch
    {
        RowState.Planned => "to make",
        RowState.UpToDate => "up to date",
        RowState.Queued => "queued",
        RowState.Running => "running",
        RowState.Done => "done",
        RowState.Skipped => "up to date",
        RowState.Failed => "FAILED",
        RowState.Cancelled => "cancelled",
        RowState.Missing => "no program",
        _ => "",
    };

    public static string Human(double seconds)
    {
        var t = TimeSpan.FromSeconds(Math.Max(0, seconds));
        if (t.TotalHours >= 1) return $"{(int)t.TotalHours}h{t.Minutes:00}m";
        if (t.TotalMinutes >= 1) return $"{t.Minutes}m{t.Seconds:00}s";
        return $"{t.Seconds}s";
    }
}
