// Kiln's window: a one-page wizard. (1) the game folder, (2) other-language copies (optional),
// (3) where the data goes (a PSP memory stick or a folder), (4) Start. Update assets, Check integrity
// and Repair sit next to Start; everything else (work folder, jobs, what to make, EBOOT, priority) is
// under "Advanced settings", pre-set for a clean full run. Every check and run is kiln.py itself (dry
// run / run / --update / --check with --json); the window builds the command line, shows the stream
// and, while kiln.py has no --target, copies the data to the memory stick itself (CardSync).
using System.Diagnostics;
using System.Text.Json;

namespace Kiln;

public sealed class MainForm : Form
{
    // ---- look ----
    private static readonly float TestScale = float.TryParse(Environment.GetEnvironmentVariable("KILN_FONT_SCALE"),
        System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var f) && f is >= 0.5f and <= 3f ? f : 1f;
    private readonly Font _ui = new("Segoe UI", 9f * TestScale);
    private readonly Font _bold = new("Segoe UI", 9f * TestScale, FontStyle.Bold);
    private readonly Font _head = new("Segoe UI", 9.75f * TestScale, FontStyle.Bold);
    private readonly Font _big = new("Segoe UI", 9.75f * TestScale, FontStyle.Bold);
    private readonly Font _mono = new("Consolas", 8.5f * TestScale);
    private static readonly Color HelpColor = Color.FromArgb(68, 68, 68);
    private static readonly Color Good = Color.FromArgb(0, 110, 40);
    private static readonly Color Bad = Color.FromArgb(180, 20, 20);
    private static readonly Color Warn = Color.FromArgb(150, 80, 0);

    // ---- (1) game ----
    private readonly TextBox _game = new() { Dock = DockStyle.Fill };
    private readonly Button _gameBrowse = new() { Text = "Browse...", AutoSize = true };
    private readonly Label _gameInfo = new();
    // ---- (2) languages ----
    private readonly ListView _extras = new()
    {
        View = View.Details, FullRowSelect = true, HeaderStyle = ColumnHeaderStyle.Nonclickable,
        Dock = DockStyle.Fill, HideSelection = false, MultiSelect = false,
    };
    private readonly Button _addExtra = new() { Text = "Add folder...", AutoSize = true };
    private readonly Button _removeExtra = new() { Text = "Remove", AutoSize = true };
    private readonly ComboBox _defaultLocale = new() { DropDownStyle = ComboBoxStyle.DropDownList };
    // ---- (3) destination ----
    private readonly RadioButton _toStick = new() { Text = "PSP memory stick:", AutoSize = true };
    private readonly RadioButton _toFolder = new() { Text = "A folder on this PC:", AutoSize = true };
    private readonly ComboBox _stick = new() { DropDownStyle = ComboBoxStyle.DropDownList, Dock = DockStyle.Fill };
    private readonly Button _stickRefresh = new() { Text = "Refresh", AutoSize = true };
    private readonly TextBox _folder = new() { Dock = DockStyle.Fill };
    private readonly Button _folderBrowse = new() { Text = "Browse...", AutoSize = true };
    private readonly Label _destInfo = new();
    private readonly FlowLayoutPanel _existRow = new() { AutoSize = true, WrapContents = true, Dock = DockStyle.Fill, Margin = new Padding(0) };
    private readonly Label _existInfo = new();
    private readonly Button _addLang = new() { Text = "Add a language..." };
    private readonly Button _removeLang = new() { Text = "Remove a language..." };
    // ---- (4) actions ----
    private readonly Button _start = new() { Text = "Start", AutoSize = true };
    private readonly Button _update = new() { Text = "Update assets", AutoSize = true };
    private readonly Button _checkInt = new() { Text = "Check integrity", AutoSize = true };
    private readonly Button _repair = new() { Text = "Repair", AutoSize = true, Enabled = false };
    private readonly Button _cancel = new() { Text = "Cancel", AutoSize = true, Enabled = false };
    private readonly ProgressBar _overall = new() { Dock = DockStyle.Fill, Maximum = 1000 };
    private readonly Label _status = new();
    // ---- advanced ----
    private readonly LinkLabel _advToggle = new() { AutoSize = true };
    private readonly TableLayoutPanel _adv = new() { ColumnCount = 1, Dock = DockStyle.Fill, AutoSize = true, Visible = false };
    private readonly TextBox _work = new() { Dock = DockStyle.Fill };
    private readonly Label _workInfo = new();
    private readonly TextBox _pcOut = new() { Dock = DockStyle.Fill };
    private readonly Label _pcOutInfo = new();
    private readonly FlowLayoutPanel _groups = new() { AutoSize = true, WrapContents = true, Dock = DockStyle.Fill, Margin = new Padding(0) };
    private readonly NumericUpDown _jobs = new() { Minimum = 1, Maximum = 64, AutoSize = true };
    private readonly CheckBox _lowPrio = new() { Text = "Low priority (the PC stays responsive)", AutoSize = true };
    private readonly CheckBox _force = new() { Text = "Rebuild finished steps", AutoSize = true };
    private readonly TextBox _eboot = new() { Dock = DockStyle.Fill };
    private readonly TextBox _server = new() { Dock = DockStyle.Fill, PlaceholderText = "host or host:port of a 3.3.5a server you may play on" };
    private readonly TextBox _app = new() { Text = "WOWEEPSP" };
    // ---- bottom ----
    private readonly TabControl _tabs = new() { Dock = DockStyle.Fill };
    private readonly StepList _steps = new() { Dock = DockStyle.Fill };
    private readonly TextBox _log = new()
    {
        Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Both, WordWrap = false, Dock = DockStyle.Fill,
        BackColor = SystemColors.Window,
    };
    private readonly TableLayoutPanel _summary = new() { ColumnCount = 1, Dock = DockStyle.Top, AutoSize = true, Padding = new Padding(10) };
    private readonly Label _sumTitle = new();
    private readonly Label _sumText = new();
    private readonly ListView _problems = new()
    {
        View = View.Details, FullRowSelect = true, HeaderStyle = ColumnHeaderStyle.Nonclickable, Dock = DockStyle.Fill, Visible = false,
    };
    private readonly Button _sumOpenOut = new() { Text = "Open the data folder", AutoSize = true };
    private readonly Button _sumOpenStick = new() { Text = "Open on the memory stick", AutoSize = true };
    private readonly Button _sumNote = new() { Text = "Personal-use note", AutoSize = true };
    private readonly Button _sumLogs = new() { Text = "Open logs", AutoSize = true };
    private readonly SplitContainer _split = new() { Dock = DockStyle.Fill, Orientation = Orientation.Horizontal, FixedPanel = FixedPanel.None };
    private readonly Panel _topScroll = new() { Dock = DockStyle.Fill, AutoScroll = true };
    private readonly TableLayoutPanel _actions = new() { ColumnCount = 1, Dock = DockStyle.Fill, AutoSize = true, Margin = new Padding(0) };
    private readonly TableLayoutPanel _form = new() { ColumnCount = 1, Dock = DockStyle.Top, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink };
    private readonly ToolTip _tips = new() { AutoPopDelay = 30000, InitialDelay = 400 };
    private readonly System.Windows.Forms.Timer _tick = new() { Interval = 1000 };
    private readonly System.Windows.Forms.Timer _debounce = new() { Interval = 700 };

    // ---- state ----
    private readonly KilnInstall? _inst;
    private KilnCaps _caps = new();
    private List<StepGroup> _groupDefs = new();
    private readonly Settings _settings = Settings.Load();
    private readonly EtaStore _eta = new();
    private CancellationTokenSource? _checkCts;
    private CancellationTokenSource? _runCts;
    private bool _busy;
    private bool _clientOk;
    private bool _ready;
    private bool _cancelled;
    private bool _userPickedLocale;                  // the player chose the start language (else: the main client's)
    private string _primary = "";
    private List<string> _kilnLocales = new();
    private DateTime _runStart;
    private string _phaseText = "";
    private double _copyFraction = -1;
    private readonly List<(string where, string path, string what)> _found = new();   // last integrity check
    private int _extraFiles;                                                           // files no step made (left alone)
    private JsonElement? _lastDone;

    public MainForm()
    {
        _inst = KilnInstall.Find();
        SuspendLayout();
        AutoScaleDimensions = new SizeF(96F, 96F);
        AutoScaleMode = AutoScaleMode.Dpi;
        Font = _ui;
        Text = "Kiln " + (_inst?.Version ?? "");
        try { Icon = Icon.ExtractAssociatedIcon(Environment.ProcessPath ?? Application.ExecutablePath); } catch { }
        StartPosition = FormStartPosition.Manual;
        MinimumSize = new Size(Px(640), Px(520));
        Size = new Size(Px(860), Px(760));                 // logical; scaled to the DPI at load, then fitted to the screen

        BuildLayout();
        WireEvents();
        ResumeLayout(true);
        LoadSettings();
        _tick.Tick += (_, _) => OnTick();
        _debounce.Tick += async (_, _) => { _debounce.Stop(); await RunCheckAsync(); };
        Load += (_, _) =>
        {
            // open on the screen the mouse is on; once its DPI has applied, fit and centre there
            var wa = Screen.FromPoint(Cursor.Position).WorkingArea;
            Location = new Point(wa.Left + 20, wa.Top + 20);
            BeginInvoke(FitToScreen);
        };
        Shown += async (_, _) => { ScaleLists(DeviceDpi / 96f); ActiveControl = _gameBrowse; FitSplit(); await InitAsync(); };
        Resize += (_, _) => { if (WindowState != FormWindowState.Minimized) FitSplit(); };
        DpiChanged += (_, e) => BeginInvoke(() => { ScaleLists(e.DeviceDpiNew / (float)e.DeviceDpiOld); FitSplit(); });
    }

    /// Sizes of controls made in the constructor are logical (96 DPI); the form's automatic scaling
    /// (AutoScaleMode.Dpi) turns them into pixels. Dev(): pixels at the current DPI, for anything made
    /// or measured after that. KILN_FONT_SCALE (a test aid) enlarges everything like a higher scaling.
    private static int Px(int logical) => (int)Math.Round(logical * TestScale);
    private int Dev(int logical) => (int)Math.Round(logical * TestScale * DeviceDpi / 96f);

    /// ListView columns do not follow the DPI by themselves.
    private void ScaleLists(float factor)
    {
        if (Math.Abs(factor - 1f) < 0.01f) return;
        foreach (var lv in new[] { _extras, _problems, (ListView)_steps })
            foreach (ColumnHeader c in lv.Columns) c.Width = (int)Math.Round(c.Width * factor);
    }

    /// At most 92 % x 94 % of the working area of the screen the window opens on, centred there.
    private void FitToScreen()
    {
        var wa = Screen.FromControl(this).WorkingArea;
        int w = Math.Min(Width, wa.Width * 92 / 100), h = Math.Min(Height, wa.Height * 94 / 100);
        MinimumSize = new Size(Math.Min(MinimumSize.Width, w), Math.Min(MinimumSize.Height, h));
        Bounds = new Rectangle(wa.Left + (wa.Width - w) / 2, wa.Top + (wa.Height - h) / 2, w, h);
    }

    // ================================================================================== layout
    private Label Heading(string text) => new() { Text = text, AutoSize = true, Font = _head, Margin = new Padding(0, Px(7), 0, Px(1)) };

    private Label Help(string text, Label? reuse = null)
    {
        var l = reuse ?? new Label();
        l.Text = text;
        l.AutoSize = true;
        l.Anchor = AnchorStyles.Left | AnchorStyles.Right | AnchorStyles.Top;   // a TableLayoutPanel wraps an anchored AutoSize label
        l.ForeColor = HelpColor;
        l.Margin = new Padding(Px(1), Px(1), 0, Px(1));
        return l;
    }

    private static TableLayoutPanel Row(params (Control c, SizeType t, float w)[] cells)
    {
        var p = new TableLayoutPanel { ColumnCount = cells.Length, RowCount = 1, AutoSize = true, Dock = DockStyle.Fill, Margin = new Padding(0) };
        p.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        for (int i = 0; i < cells.Length; i++)
        {
            p.ColumnStyles.Add(new ColumnStyle(cells[i].t, cells[i].w));
            p.Controls.Add(cells[i].c, i, 0);
            if (cells[i].c is Label or CheckBox or RadioButton) cells[i].c.Anchor = AnchorStyles.Left;
        }
        return p;
    }

    private static (Control, SizeType, float) Fill(Control c) => (c, SizeType.Percent, 100f);
    private static (Control, SizeType, float) Auto(Control c) => (c, SizeType.AutoSize, 0f);

    private static FlowLayoutPanel Flow(params Control[] cs)
    {
        var f = new FlowLayoutPanel { AutoSize = true, WrapContents = true, Dock = DockStyle.Fill, Margin = new Padding(0) };
        f.Controls.AddRange(cs);
        return f;
    }

    /// A standard button (75 x 23 at 96 DPI, wider when its text needs it).
    private static Button Std(Button b, int minWidth = 75)
    {
        b.AutoSize = true;
        b.MinimumSize = new Size(Px(minWidth), Px(23));
        b.Padding = new Padding(Px(4), 0, Px(4), 0);
        return b;
    }

    private Label Inline(string text, bool help = false) =>
        new() { Text = text, AutoSize = true, Anchor = AnchorStyles.Left, ForeColor = help ? HelpColor : SystemColors.ControlText, Margin = new Padding(Px(3), Px(5), Px(3), 0) };

    private void Add(Control c) { _form.RowStyles.Add(new RowStyle(SizeType.AutoSize)); _form.Controls.Add(c); }

    private void BuildLayout()
    {
        _form.Padding = new Padding(Px(9), Px(2), Px(9), Px(4));
        _form.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));

        // (1) game folder
        Std(_gameBrowse).Click += (_, _) => PickGame();
        Add(Heading("1   Your game folder"));
        Add(Row(Fill(_game), Auto(_gameBrowse)));
        _gameInfo.AutoSize = true;
        _gameInfo.Anchor = AnchorStyles.Left | AnchorStyles.Right | AnchorStyles.Top;
        _gameInfo.Font = _bold;
        _gameInfo.Margin = new Padding(Px(1), Px(2), 0, 0);
        Add(_gameInfo);

        // (2) other languages
        Add(Heading("2   Extra languages (optional)"));
        _extras.Columns.Add("other-language copy of the client", Px(430));
        _extras.Columns.Add("languages found", Px(130));
        _extras.Height = Px(56);
        Std(_addExtra, 90); Std(_removeExtra, 90);
        var extraBtns = new FlowLayoutPanel { FlowDirection = FlowDirection.TopDown, AutoSize = true, WrapContents = false, Margin = new Padding(Px(3), 0, 0, 0) };
        extraBtns.Controls.Add(_addExtra);
        extraBtns.Controls.Add(_removeExtra);
        Add(Row(Fill(_extras), Auto(extraBtns)));
        _defaultLocale.Width = Px(90);
        Add(Flow(Inline("The game starts in:"), _defaultLocale, Inline("(players can switch the language in the game)", help: true)));

        // (3) destination
        Add(Heading("3   Where to put it"));
        var dest = new TableLayoutPanel { ColumnCount = 3, RowCount = 2, AutoSize = true, Dock = DockStyle.Fill, Margin = new Padding(0) };
        dest.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        dest.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        dest.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        dest.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        dest.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        _toStick.Anchor = _toFolder.Anchor = AnchorStyles.Left;
        Std(_stickRefresh); Std(_folderBrowse);
        dest.Controls.Add(_toStick, 0, 0); dest.Controls.Add(_stick, 1, 0); dest.Controls.Add(_stickRefresh, 2, 0);
        dest.Controls.Add(_toFolder, 0, 1); dest.Controls.Add(_folder, 1, 1); dest.Controls.Add(_folderBrowse, 2, 1);
        Add(dest);
        _destInfo.AutoSize = true;
        _destInfo.Anchor = AnchorStyles.Left | AnchorStyles.Right | AnchorStyles.Top;
        _destInfo.Margin = new Padding(Px(1), Px(1), 0, 0);
        Add(_destInfo);
        _existInfo.AutoSize = true;
        _existInfo.Font = _bold;
        _existInfo.Margin = new Padding(Px(1), Px(6), Px(6), 0);
        Std(_addLang, 90); Std(_removeLang, 90);
        _existRow.Controls.AddRange(new Control[] { _existInfo, _addLang, _removeLang });
        _existRow.Visible = false;
        Add(_existRow);

        // advanced (in the scrolling part; step 4 stays pinned below it)
        _advToggle.Margin = new Padding(0, Px(5), 0, 0);
        Add(_advToggle);
        BuildAdvanced();
        Add(_adv);
        SetAdvanced(false);

        _topScroll.Controls.Add(_form);
        _split.Panel1.Controls.Add(_topScroll);

        // bottom: steps / log / summary
        var tSteps = new TabPage("Steps") { Padding = new Padding(Px(2)) };
        tSteps.Controls.Add(_steps);
        var tLog = new TabPage("Log") { Padding = new Padding(Px(2)) };
        _log.Font = _mono;
        tLog.Controls.Add(_log);
        var tSum = new TabPage("Summary") { AutoScroll = true };
        BuildSummary();
        tSum.Controls.Add(_summary);
        _tabs.TabPages.AddRange(new[] { tSteps, tLog, tSum });

        // (4) actions: pinned between the wizard and the tabs, so Start is always in view
        _actions.Padding = new Padding(Px(9), 0, Px(9), Px(2));
        _actions.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        void P(Control c) { _actions.RowStyles.Add(new RowStyle(SizeType.AutoSize)); _actions.Controls.Add(c); }
        var h4 = Heading("4   Make the data");
        h4.Margin = new Padding(0, Px(3), 0, Px(1));
        P(h4);
        _start.Font = _big;
        Std(_start, 100).MinimumSize = new Size(Px(100), Px(27));
        foreach (var b in new[] { _update, _checkInt, _repair, _cancel }) Std(b, 85).MinimumSize = new Size(Px(85), Px(27));
        P(Flow(_start, _update, _checkInt, _repair, _cancel));
        _overall.Height = Px(14);
        _overall.Margin = new Padding(Px(3), Px(3), Px(3), Px(1));
        P(_overall);
        _status.AutoSize = false;                 // one line; the whole text is in the tooltip
        _status.AutoEllipsis = true;
        _status.Dock = DockStyle.Fill;
        _status.Height = Px(19);
        _status.TextAlign = ContentAlignment.MiddleLeft;
        _status.Margin = new Padding(Px(3), 0, 0, Px(1));
        P(_status);
        // a fixed row: an AutoSize row would ask the label for its height at a tiny trial width (many lines)
        _actions.RowStyles[_actions.RowStyles.Count - 1] = new RowStyle(SizeType.Absolute, Px(21));
        var lower = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 2, Margin = new Padding(0) };
        lower.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        lower.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        lower.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        lower.Controls.Add(_actions, 0, 0);
        var tabsHost = new Panel { Dock = DockStyle.Fill, Padding = new Padding(Px(9), 0, Px(9), 0), Margin = new Padding(0) };
        tabsHost.Controls.Add(_tabs);
        lower.Controls.Add(tabsHost, 0, 1);
        _split.Panel2.Controls.Add(lower);
        _split.SplitterWidth = Px(4);

        var footer = new Label
        {
            AutoSize = true, Anchor = AnchorStyles.Left | AnchorStyles.Right, ForeColor = HelpColor,
            Margin = new Padding(Px(9), Px(3), Px(9), Px(4)),
            Text = "Personal use only: the data comes from your own copy of the game - do not share, upload or sell it. Kiln sends nothing anywhere.",
        };
        var root = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 2 };
        root.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        root.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        root.Controls.Add(_split, 0, 0);
        root.Controls.Add(footer, 0, 1);
        Controls.Add(root);
        _steps.ScaleColumns(TestScale);

        _tips.SetToolTip(_game, "The folder of your own 3.3.5a (build 12340) PC client: it holds the game's executable and the Data folder.");
        _tips.SetToolTip(_gameBrowse, "Choose the folder of your own 3.3.5a (build 12340) PC client.");
        _tips.SetToolTip(_extras, "Optional: other copies of the same client in another language. The game can then switch between them.");
        _tips.SetToolTip(_addExtra, "Add another copy of the client in another language (same build).");
        _tips.SetToolTip(_toStick, "Kiln makes the data on this PC first (Advanced settings) and copies it to the memory stick; an update copies only what changed.");
        _tips.SetToolTip(_toFolder, "Kiln writes the data folder here; copy it to the memory stick as PSP/GAME/<folder>/data later.");
        _tips.SetToolTip(_start, "Make the data. A run that was cancelled or failed resumes: finished steps are skipped.");
        _tips.SetToolTip(_checkInt, "Check the data folder (and the memory-stick copy) against Kiln's manifest: missing files, wrong sizes, wrong hashes.");
        _tips.SetToolTip(_repair, "Re-make or re-copy only the files the integrity check found bad.");
        _tips.SetToolTip(_steps, "Double-click a step to open its log.");
    }

    private void BuildAdvanced()
    {
        _adv.Margin = new Padding(Px(12), 0, 0, 0);
        _adv.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        void A(Control c) { _adv.RowStyles.Add(new RowStyle(SizeType.AutoSize)); _adv.Controls.Add(c); }
        Label L(string t) => new() { Text = t, AutoSize = true, Font = _bold, Margin = new Padding(0, Px(6), 0, 0) };
        Button B(string t, EventHandler h) { var b = Std(new Button { Text = t }); b.Click += h; return b; }

        A(L("Work folder (extracted client files, about 10 GB; a fast local disk helps)"));
        A(Row(Fill(_work), Auto(B("Browse...", (_, _) => PickFolder(_work, "Work folder for the extracted client files (about 10 GB)")))));
        _workInfo.AutoSize = true; _workInfo.Anchor = AnchorStyles.Left | AnchorStyles.Right; _workInfo.ForeColor = HelpColor;
        A(_workInfo);
        _tips.SetToolTip(_work, "Kiln extracts the files it needs from your client here (the client itself is only read). " +
                                "Keep it for faster re-runs and updates; delete it when you no longer need it.");

        A(L("Data folder on this PC, when the data goes to a memory stick"));
        A(Row(Fill(_pcOut), Auto(B("Browse...", (_, _) => PickFolder(_pcOut, "Where Kiln makes the data on this PC")))));
        _pcOutInfo.AutoSize = true; _pcOutInfo.Anchor = AnchorStyles.Left | AnchorStyles.Right; _pcOutInfo.ForeColor = HelpColor;
        A(_pcOutInfo);
        _tips.SetToolTip(_pcOut, "Kiln makes the data here first and then copies it to the memory stick, so an update copies only what changed.");

        A(L("What to make (everything the port uses is ticked)"));
        A(_groups);

        A(L("Speed"));
        A(Flow(Inline("Jobs:"), _jobs, Inline($"(default {Hardware.DefaultJobs()}: one per CPU core, one thread left free)", help: true), _lowPrio, _force));
        _tips.SetToolTip(_jobs, "Steps that run at the same time (and workers for the converters that take a count).");

        A(L("Your EBOOT.PBP of the port (optional: Kiln makes a copy with menu art from your client)"));
        A(Row(Fill(_eboot), Auto(B("Browse...", (_, _) => PickEboot()))));
        _tips.SetToolTip(_eboot, "Your copy of the port's EBOOT.PBP. Kiln writes a copy with a menu icon and background made from your client's art and puts it on the memory stick.");

        A(L("Server (optional: written to wowee.cfg of the game folder as server=)"));
        A(_server);
        _tips.SetToolTip(_server, "The realm list address the game connects to, e.g. 127.0.0.1 or logon.example.org (host:port for another port). Kiln writes only server= / port= into wowee.cfg; the account is typed on the PSP's login screen.");

        _app.Width = Px(110);
        A(Flow(Inline("Game folder on the memory stick:  PSP/GAME/"), _app,
               B("Check plan (dry run)", async (_, _) => await RunCheckAsync(verbose: true)),
               B("Copy command", (_, _) => CopyCommand()),
               B("Open logs", (_, _) => OpenFolder(Path.Combine(PcOut(), "logs")))));
    }

    private void BuildSummary()
    {
        _summary.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        void S(Control c) { _summary.RowStyles.Add(new RowStyle(SizeType.AutoSize)); _summary.Controls.Add(c); }
        _sumTitle.AutoSize = true; _sumTitle.Font = _head; _sumTitle.Text = "Nothing has run yet.";
        _sumTitle.Anchor = AnchorStyles.Left | AnchorStyles.Right;
        _sumText.AutoSize = true; _sumText.Anchor = AnchorStyles.Left | AnchorStyles.Right; _sumText.Margin = new Padding(0, Px(4), 0, Px(4));
        S(_sumTitle);
        S(_sumText);
        _problems.Columns.Add("where", Px(100));
        _problems.Columns.Add("file", Px(360));
        _problems.Columns.Add("problem", Px(240));
        _problems.Height = Px(120);
        S(_problems);
        foreach (var b in new[] { _sumOpenOut, _sumOpenStick, _sumNote, _sumLogs }) Std(b);
        S(Flow(_sumOpenOut, _sumOpenStick, _sumNote, _sumLogs));
        S(Help("Personal use only: the data folder is made from your own copy of the game and contains material that belongs " +
               "to the game's rights holder. Use it on your own device; do not share, upload or sell it, or any archive or " +
               "build that contains it. The work folder holds files extracted from your client: delete it when you no longer need it."));
    }

    private void SetAdvanced(bool open)
    {
        _adv.Visible = open;
        _advToggle.Text = open ? "Hide advanced settings" : "Advanced settings (work folder, jobs, what to make, EBOOT)";
        if (IsHandleCreated) FitSplit();
    }

    /// The top (the wizard) gets the height it needs, up to what leaves the steps and log a usable
    /// height; past that it scrolls.
    private void FitSplit()
    {
        if (!IsHandleCreated) return;
        try
        {
            int want = _form.GetPreferredSize(new Size(_topScroll.ClientSize.Width, 0)).Height + Dev(4);
            int lower = _actions.GetPreferredSize(new Size(Math.Max(_actions.Width, _split.Width - Dev(20)), 0)).Height + Dev(130);   // step 4 + a usable step list
            int max = Math.Max(Dev(100), _split.Height - lower);
            _split.SplitterDistance = Math.Clamp(want, Dev(100), max);
        }
        catch (InvalidOperationException) { }
    }

    private void WireEvents()
    {
        _game.TextChanged += (_, _) => { ScanPrimary(); ScheduleCheck(); };
        _folder.TextChanged += (_, _) => { UpdateDestInfo(); ScheduleCheck(); };
        _pcOut.TextChanged += (_, _) => { UpdateDestInfo(); ScheduleCheck(); };
        _work.TextChanged += (_, _) => UpdateDestInfo();
        _app.TextChanged += (_, _) => UpdateDestInfo();
        _stick.SelectedIndexChanged += (_, _) => { if (_stick.SelectedItem is Hardware.Stick && _ready) _toStick.Checked = true; UpdateDestInfo(); ScheduleCheck(); };
        _toStick.CheckedChanged += (_, _) => { UpdateDestInfo(); ScheduleCheck(); };
        _toFolder.CheckedChanged += (_, _) => { UpdateDestInfo(); ScheduleCheck(); };
        _stickRefresh.Click += (_, _) => RefreshSticks();
        _folderBrowse.Click += (_, _) => { PickFolder(_folder, "Where Kiln writes the data folder"); _toFolder.Checked = true; };
        _addExtra.Click += (_, _) => AddExtra();
        _removeExtra.Click += (_, _) => RemoveExtra();
        _defaultLocale.SelectionChangeCommitted += (_, _) => { _userPickedLocale = true; if (_ready) ScheduleCheck(); };
        _addLang.Click += async (_, _) => await AddLanguageAsync();
        _removeLang.Click += async (_, _) => await RemoveLanguageAsync();
        _advToggle.LinkClicked += (_, _) => SetAdvanced(!_adv.Visible);
        _start.Click += async (_, _) => await MakeAsync(KilnMode.Run);
        _update.Click += async (_, _) => await MakeAsync(KilnMode.Update);
        _checkInt.Click += async (_, _) => await CheckIntegrityAsync();
        _repair.Click += async (_, _) => await RepairAsync();
        _cancel.Click += (_, _) => CancelRun();
        _sumOpenOut.Click += (_, _) => OpenFolder(DataDir());
        _sumOpenStick.Click += (_, _) => OpenFolder(StickData() ?? "");
        _sumNote.Click += (_, _) => OpenNote();
        _sumLogs.Click += (_, _) => OpenFolder(Path.Combine(PcOut(), "logs"));
        _steps.DoubleClick += (_, _) => OpenStepLog();
        FormClosing += OnClosing;
    }

    // WM_DEVICECHANGE: a memory stick was plugged in or out
    protected override void WndProc(ref Message m)
    {
        base.WndProc(ref m);
        if (m.Msg == 0x0219 && _ready && !_busy) BeginInvoke(RefreshSticks);
    }

    // ================================================================================== settings
    private void LoadSettings()
    {
        var s = _settings;
        bool first = s.Jobs <= 0;
        _jobs.Value = Math.Clamp(first ? Hardware.DefaultJobs() : s.Jobs, 1, 64);
        _app.Text = string.IsNullOrWhiteSpace(s.App) ? "WOWEEPSP" : s.App;
        _lowPrio.Checked = first || s.LowPriority;                      // first start: on
        _force.Checked = false;
        _eboot.Text = s.Eboot;
        _server.Text = s.Server;
        var drives = Hardware.WorkDrives(Disk.WorkBytes + Disk.DataBytes);
        string best = drives.Count > 0 ? drives[0].Root : Path.GetPathRoot(AppContext.BaseDirectory) ?? "";
        _work.Text = s.Work.Length > 0 ? s.Work : Path.Combine(best, "Kiln-work");
        _pcOut.Text = s.PcOut.Length > 0 ? s.PcOut : Path.Combine(best, "Kiln-output");
        _folder.Text = s.Out.Length > 0 ? s.Out : Path.Combine(best, "Kiln-output");
        _userPickedLocale = !string.IsNullOrEmpty(s.DefaultLocale);
        foreach (var d in s.Extra.Where(Directory.Exists)) AddExtraRow(d);
        SetAdvanced(s.Advanced);
        RefreshSticks();
        if (s.Dest == "folder" || _stick.SelectedItem is not Hardware.Stick) _toFolder.Checked = true;
        else _toStick.Checked = true;
        _game.Text = s.Game;
    }

    private void SaveSettings()
    {
        var s = _settings;
        s.Game = _game.Text.Trim();
        s.Extra = ExtraDirs();
        s.DefaultLocale = _userPickedLocale ? _defaultLocale.SelectedItem as string ?? "" : "";
        s.Dest = _toStick.Checked ? "stick" : "folder";
        s.Stick = (_stick.SelectedItem as Hardware.Stick)?.Root ?? s.Stick;
        s.Out = _folder.Text.Trim();
        s.PcOut = _pcOut.Text.Trim();
        s.Work = _work.Text.Trim();
        s.App = _app.Text.Trim();
        s.Eboot = _eboot.Text.Trim();
        s.Server = _server.Text.Trim();
        s.Jobs = (int)_jobs.Value;
        s.LowPriority = _lowPrio.Checked;
        s.Advanced = _adv.Visible;
        s.Groups = _groups.Controls.OfType<CheckBox>().Where(c => c.Checked).Select(c => ((StepGroup)c.Tag!).Id).ToList();
        s.Save();
    }

    private async Task InitAsync()
    {
        if (_inst is null)
        {
            SetStatus("kiln.py was not found next to Kiln.exe. Unzip the whole Kiln folder and start Kiln.exe from it.", Bad);
            foreach (var b in new[] { _start, _update, _checkInt }) b.Enabled = false;
            return;
        }
        Log($"Kiln {_inst.Version}  ({(_inst.Bundled ? "bundled Python" : "Python: " + _inst.Python)}, kiln.py in {_inst.Root})");
        if (_inst.ToolsOverride is not null)
            Log("converters: " + _inst.ToolsOverride + " (development tree: bin has none)" + (_inst.DllDir is null ? "" : "; runtime DLLs: " + _inst.DllDir));
        SetStatus("Starting Kiln...", null);
        _caps = await KilnCli.ReadCapsAsync(_inst);
        _groupDefs = await KilnCli.ReadGroupsAsync(_inst);
        if (_caps.Options.Count == 0)
        {
            SetStatus($"Kiln could not be started with {_inst.Python}. " + (_inst.Bundled ? "The python folder of the bundle may be damaged: unzip Kiln again." :
                      "Install Python 3.11 or later with Pillow and numpy, or use the Kiln bundle."), Bad);
            return;
        }
        if (!_caps.Has("--add-locale")) _tips.SetToolTip(_extras, "This kiln.py has no --add-locale.");
        foreach (var o in new[] { "--update", "--check", "--target" })
            if (!_caps.Has(o)) Log($"note: this kiln.py has no {o}; the window " + o switch
            {
                "--update" => "updates with a resume run (re-makes what changed) and copies the changed files itself",
                "--check" => "checks with --verify and compares the memory-stick copy itself",
                _ => "copies the data to the memory stick itself",
            });

        _groups.SuspendLayout();
        foreach (var g in _groupDefs)
        {
            bool on = _settings.Groups.Count > 0 ? _settings.Groups.Contains(g.Id) : g.Default;
            var cb = new CheckBox { Text = g.Label, AutoSize = true, Checked = on, Tag = g, Margin = new Padding(0, Dev(2), Dev(18), Dev(2)) };
            _tips.SetToolTip(cb, g.Steps.Count > 0 ? "steps: " + string.Join(", ", g.Steps)
                                                     : "terrain, objects and minimap of every map of this group (read from your client's map table)");
            cb.CheckedChanged += (_, _) => ScheduleCheck();
            _groups.Controls.Add(cb);
        }
        _groups.ResumeLayout();
        _ready = true;
        ScanPrimary();
        UpdateDestInfo();
        FitSplit();
        if (_game.Text.Trim().Length == 0) SetStatus("Start with step 1: choose your game folder.", null);
        await RunCheckAsync();
        BeginInvoke(FitSplit);
    }

    // ================================================================================== inputs
    private void PickGame()
    {
        using var dlg = new FolderBrowserDialog { Description = "Your game folder: the one that holds the game's executable and the Data folder", UseDescriptionForTitle = true };
        if (Directory.Exists(_game.Text)) dlg.InitialDirectory = _game.Text;
        if (dlg.ShowDialog(this) == DialogResult.OK) _game.Text = dlg.SelectedPath;
    }

    private void PickFolder(TextBox box, string title)
    {
        using var dlg = new FolderBrowserDialog { Description = title, UseDescriptionForTitle = true };
        try
        {
            if (Directory.Exists(box.Text)) dlg.InitialDirectory = box.Text;
            else if (box.Text.Length > 0 && Directory.GetParent(box.Text) is { Exists: true } p) dlg.InitialDirectory = p.FullName;
        }
        catch (Exception) { }
        if (dlg.ShowDialog(this) == DialogResult.OK) box.Text = dlg.SelectedPath;
    }

    private void PickEboot()
    {
        using var dlg = new OpenFileDialog { Filter = "PSP executable (EBOOT.PBP)|*.PBP|All files|*.*", Title = "Your copy of the port's EBOOT.PBP" };
        if (dlg.ShowDialog(this) == DialogResult.OK) _eboot.Text = dlg.FileName;
    }

    private void AddExtra()
    {
        using var dlg = new FolderBrowserDialog { Description = "Another copy of the client in another language (same build)", UseDescriptionForTitle = true };
        if (dlg.ShowDialog(this) != DialogResult.OK) return;
        string d = dlg.SelectedPath;
        if (Same(d, _game.Text))
        {
            MessageBox.Show(this, "That is your game folder (step 1). Add a copy in another language.", "Kiln", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }
        if (ExtraDirs().Any(x => Same(x, d))) return;
        AddExtraRow(d);
        _kilnLocales.Clear();
        RefreshLocales();
        ScheduleCheck();
    }

    private static bool Same(string a, string b)
    {
        try { return string.Equals(Path.GetFullPath(a).TrimEnd('\\'), Path.GetFullPath(b).TrimEnd('\\'), StringComparison.OrdinalIgnoreCase); }
        catch (Exception) { return false; }
    }

    private void AddExtraRow(string d)
    {
        var locs = ClientScan.Locales(d);
        var it = new ListViewItem(new[] { d, locs.Count > 0 ? string.Join(", ", locs) : "no language found (Data\\xxYY\\locale-xxYY.MPQ)" }) { Tag = d };
        if (locs.Count == 0) it.ForeColor = Bad;
        _extras.Items.Add(it);
    }

    private void RemoveExtra()
    {
        foreach (ListViewItem it in _extras.SelectedItems) _extras.Items.Remove(it);
        _kilnLocales.Clear();
        RefreshLocales();
        ScheduleCheck();
    }

    private List<string> ExtraDirs() => _extras.Items.Cast<ListViewItem>().Select(i => (string)i.Tag!).ToList();

    private void ScanPrimary()
    {
        if (!_ready) return;
        _clientOk = false;
        _kilnLocales.Clear();
        _primary = "";
        _userPickedLocale = false;                       // a new main folder: the game starts in its language
        string g = _game.Text.Trim();
        if (g.Length == 0) SetGameInfo("No game folder chosen yet.", null);
        else if (!Directory.Exists(g)) SetGameInfo("This folder does not exist.", false);
        else if (!ClientScan.HasData(g)) SetGameInfo("No Data folder here. Choose the folder that holds the game's executable and its Data folder.", false);
        else
        {
            var locs = ClientScan.Locales(g);
            if (locs.Count == 0) SetGameInfo("No language archive in Data (Data\\xxYY\\locale-xxYY.MPQ): this does not look like a complete client.", false);
            else SetGameInfo("Checking the client (" + string.Join(", ", locs) + ")...", null);
        }
        RefreshLocales();
        UpdateButtons();
    }

    /// The language the game starts in: the main client's language (Kiln's primary: the only one, else
    /// English, else the first by name) until the player picks another one in the list. Adding or
    /// removing other-language folders never changes it; a new main folder resets it.
    private void RefreshLocales()
    {
        var all = new List<string>();
        var main = Directory.Exists(_game.Text) ? ClientScan.Locales(_game.Text) : new List<string>();
        if (_kilnLocales.Count > 0) all.AddRange(_kilnLocales);
        else
        {
            all.AddRange(main);
            foreach (var d in ExtraDirs()) all.AddRange(ClientScan.Locales(d));
        }
        all = all.Distinct().ToList();
        string primary = _primary.Length > 0 ? _primary : ClientScan.Primary(main);
        string? want = _userPickedLocale ? (_defaultLocale.SelectedItem as string ?? _settings.DefaultLocale) : primary;
        _defaultLocale.BeginUpdate();
        _defaultLocale.Items.Clear();
        foreach (var l in all) _defaultLocale.Items.Add(l);
        _defaultLocale.EndUpdate();
        int i = !string.IsNullOrEmpty(want) ? all.IndexOf(want) : -1;
        if (i < 0) { i = all.IndexOf(primary); _userPickedLocale = false; }
        if (i < 0 && all.Count > 0) i = 0;
        if (i >= 0) _defaultLocale.SelectedIndex = i;
    }

    private void RefreshSticks()
    {
        string? keep = (_stick.SelectedItem as Hardware.Stick)?.Root ?? _settings.Stick;
        var sticks = Hardware.Sticks();
        _stick.BeginUpdate();
        _stick.Items.Clear();
        foreach (var s in sticks) _stick.Items.Add(s);
        if (sticks.Count == 0) _stick.Items.Add("none found: insert the memory stick (or connect the PSP in USB mode), then Refresh");
        _stick.EndUpdate();
        int i = sticks.FindIndex(s => string.Equals(s.Root, keep, StringComparison.OrdinalIgnoreCase));
        _stick.SelectedIndex = i >= 0 ? i : 0;
        _toStick.Enabled = sticks.Count > 0 && !_busy;
        if (sticks.Count == 0 && _toStick.Checked) _toFolder.Checked = true;
        UpdateDestInfo();
    }

    // ---- where things go ----
    private Hardware.Stick? StickChoice() => _toStick.Checked ? _stick.SelectedItem as Hardware.Stick : null;
    private string AppName() => string.IsNullOrWhiteSpace(_app.Text) ? "WOWEEPSP" : _app.Text.Trim();

    /// kiln.py's --out: the chosen folder, or the PC data folder when the data goes to a memory stick.
    private string PcOut() => (StickChoice() is not null ? _pcOut.Text : _folder.Text).Trim();
    private string DataDir() => PcOut().Length > 0 ? Path.Combine(PcOut(), "data") : "";
    private string? StickGameDir() => StickChoice() is { } s ? Path.Combine(s.Root, "PSP", "GAME", AppName()) : null;
    private string? StickData() => StickGameDir() is { } g ? Path.Combine(g, "data") : null;
    private string WorkDir() => _work.Text.Trim();

    private void UpdateDestInfo()
    {
        if (!_ready) return;
        var parts = new List<string>();
        Color color = HelpColor;
        string data = DataDir();
        var stick = StickChoice();
        // one line: where the data goes, then the free space (the details are in Advanced settings)
        var od = Disk.DriveOf(PcOut());
        string pcFree = "";
        try { if (od is { IsReady: true }) pcFree = $"{od.Name.TrimEnd('\\')} {Disk.Gb(od.AvailableFreeSpace)}"; } catch (IOException) { }
        var (_, low) = SpaceText();
        if (stick is not null)
        {
            parts.Add($"Data -> {StickData()} (made in {PcOut()} first).");
            parts.Add($"Free: stick {Disk.Gb(stick.Free)}{(pcFree.Length > 0 ? ", " + pcFree : "")} (needs ~10 GB / ~20 GB).");
            if (stick.Free < Disk.DataBytes) color = Warn;
            if (!string.Equals(stick.Format, "FAT32", StringComparison.OrdinalIgnoreCase) && !string.Equals(stick.Format, "exFAT", StringComparison.OrdinalIgnoreCase))
            { parts.Add($"The stick is {stick.Format}; the PSP reads FAT32."); color = Warn; }
        }
        else if (data.Length > 0)
        {
            parts.Add($"Data -> {data} (copy it to the stick as PSP/GAME/{AppName()}/data).");
            if (pcFree.Length > 0) parts.Add($"Free: {pcFree} (needs ~20 GB).");
        }
        else { parts.Add("Choose where the data goes."); color = Warn; }
        if (low) color = Warn;
        _destInfo.Text = string.Join("  ", parts);
        _destInfo.ForeColor = color;
        ShowExisting();
        _workInfo.Text = DriveLine(WorkDir(), Disk.WorkBytes, "the work folder");
        _pcOutInfo.Text = DriveLine(_pcOut.Text.Trim(), Disk.DataBytes, "the data");
        if (IsHandleCreated) FitSplit();
    }

    private static string DriveLine(string path, long need, string what)
    {
        var d = Disk.DriveOf(path);
        if (path.Length == 0 || d is null) return "";
        try
        {
            if (!d.IsReady) return $"Drive {d.Name} is not ready.";
            string kind = Hardware.KindText(Hardware.KindOf(d.RootDirectory.FullName));
            return $"{d.Name.TrimEnd('\\')} is a {kind} with {Disk.Gb(d.AvailableFreeSpace)} free ({what} needs about {Disk.Gb(need)})" +
                   (d.AvailableFreeSpace < need ? " - NOT ENOUGH." : ".");
        }
        catch (IOException) { return ""; }
    }

    /// Free space on the PC drives Kiln writes to (data + work on one drive count together).
    private (string text, bool low) SpaceText()
    {
        var od = Disk.DriveOf(PcOut());
        var wd = Disk.DriveOf(WorkDir());
        if (od is null || PcOut().Length == 0) return ("", false);
        try
        {
            bool same = wd is not null && string.Equals(od.Name, wd.Name, StringComparison.OrdinalIgnoreCase);
            long need = Disk.DataBytes + (same ? Disk.WorkBytes : 0);
            if (!od.IsReady) return ($"Drive {od.Name} is not ready.", true);
            bool low = od.AvailableFreeSpace < need;
            string t = $"{od.Name.TrimEnd('\\')} {Disk.Gb(od.AvailableFreeSpace)} free (needs ~{Disk.Gb(need)}{(same ? " with the work folder" : "")}).";
            if (!same && wd is { IsReady: true })
            {
                low |= wd.AvailableFreeSpace < Disk.WorkBytes;
                t += $" Work {wd.Name.TrimEnd('\\')} {Disk.Gb(wd.AvailableFreeSpace)} free (needs about {Disk.Gb(Disk.WorkBytes)}).";
            }
            return (t, low);
        }
        catch (IOException) { return ("", false); }
    }

    private RunOptions Options(KilnMode mode)
    {
        var o = new RunOptions
        {
            Mode = mode,
            Game = _game.Text.Trim(),
            Out = PcOut(),
            Work = WorkDir().Length > 0 ? WorkDir() : null,
            Target = StickGameDir(),
            AddLocales = ExtraDirs(),
            Jobs = (int)_jobs.Value,
            Force = _force.Checked,
            Eboot = File.Exists(_eboot.Text.Trim()) ? _eboot.Text.Trim() : null,
            Server = _server.Text.Trim().Length > 0 ? _server.Text.Trim() : null,
            App = AppName(),
            Steps = KilnCli.StepTokens(_groups.Controls.OfType<CheckBox>().Where(c => c.Checked).Select(c => (StepGroup)c.Tag!)),
        };
        string? dl = _defaultLocale.SelectedItem as string;
        if (_userPickedLocale && !string.IsNullOrEmpty(dl) && dl != _primary) o.DefaultLocale = dl;
        if (o.Out.Length == 0) o.Out = Path.Combine(Path.GetTempPath(), "kiln-check");     // dry run only: writes nothing
        return o;
    }

    // ================================================================================== dry run
    private void ScheduleCheck()
    {
        if (!_ready || _busy) return;
        _debounce.Stop();
        _debounce.Start();
    }

    private async Task RunCheckAsync(bool verbose = false, bool keepStatus = false)
    {
        if (_inst is null || !_ready || _busy) return;
        _checkCts?.Cancel();
        var cts = _checkCts = new CancellationTokenSource();
        string g = _game.Text.Trim();
        if (g.Length == 0 || !Directory.Exists(g) || !ClientScan.HasData(g)) { UpdateButtons(); return; }
        var args = KilnCli.Args(Options(KilnMode.DryRun), _inst, _caps);
        if (verbose) { Log("$ " + KilnCli.Show(_inst, args)); _tabs.SelectedIndex = 1; }

        var proc = new KilnProcess();
        JsonElement? client = null, plan = null;
        string? error = null;
        var text = new List<string>();
        proc.Event += (kind, e) =>
        {
            if (kind == "client") client = e;
            else if (kind == "plan") plan = e;
            else if (kind == "error") error = e.Str("message");
            else if (kind == "log") text.Add(e.Str("text") ?? "");
        };
        proc.Text += t => text.Add(t);
        int rc = await proc.RunAsync(_inst, args, cts.Token);
        if (cts.IsCancellationRequested || _busy) return;

        if (verbose || rc != 0) foreach (var t in text) Log(t);
        if (client is { } c) ApplyClient(c);
        if (error is not null || (rc != 0 && client is null))
        {
            _clientOk = client is not null;
            string msg = error ?? text.LastOrDefault(t => t.Trim().Length > 0) ?? $"kiln.py stopped with exit code {rc}";
            if (client is null) SetGameInfo(msg, false);
            SetStatus(msg.Split('\n')[0], Bad);
        }
        else if (plan is { } p)
        {
            ShowPlan(p, running: false);
            if (keepStatus) { UpdateButtons(); return; }
            int todo = _steps.Rows.Count(r => r.State == RowState.Planned);
            int done = _steps.Rows.Count(r => r.State == RowState.UpToDate);
            int missing = _steps.Rows.Count(r => r.State == RowState.Missing);
            string maps = p.Str("maps_note") is not null ? " The maps are added once Kiln has read the client's map table." : "";
            if (missing > 0) SetStatus($"{missing} step(s) need a converter program that is missing (see Steps). Is the bin folder complete?", Bad);
            else if (todo == 0 && done > 0) SetStatus($"Everything ticked is up to date ({done} steps). Update assets copies anything missing to the memory stick.", Good);
            else SetStatus($"Ready: press Start. {todo} step(s) to make{(done > 0 ? $", {done} already up to date" : "")}.{maps}", null);
        }
        UpdateButtons();
    }

    private void ApplyClient(JsonElement c)
    {
        _clientOk = true;
        _primary = c.Str("primary") ?? "";
        _kilnLocales = c.Strs("locales").ToList();
        string version = (c.Str("label") ?? "").Split(',')[0];
        var extra = c.Strs("extra").ToList();
        string def = c.Str("default") ?? _primary;
        string langs = "language " + _primary + (extra.Count > 0 ? ", extra " + string.Join(", ", extra) : "");
        SetGameInfo($"Found: {version}, {langs}. The game starts in {def}.", true);
        RefreshLocales();
    }

    private void ShowPlan(JsonElement p, bool running)
    {
        if (!running) _steps.ClearRows();
        _steps.BeginUpdate();
        foreach (var s in p.Arr("steps"))
        {
            string name = s.Str("name") ?? "?";
            string group = s.Str("group") ?? "";
            string state = s.Str("state") ?? "run";
            double? hist = s.Num("last_seconds") ?? _eta.Get(name);
            _steps.Upsert(new StepRow
            {
                Name = name, Group = group, Locale = s.Str("locale") ?? "", Title = s.Str("title") ?? "",
                Weight = hist ?? EtaStore.Guess(name, group), HistoryWeight = hist is not null,
                State = state switch
                {
                    "up-to-date" => running && _force.Checked ? RowState.Queued : RowState.UpToDate,
                    "program-missing" => RowState.Missing,
                    _ => running ? RowState.Queued : RowState.Planned,
                },
            });
        }
        _steps.EndUpdate();
    }

    // ================================================================================== actions
    private bool InputsOk()
    {
        void Msg(string t) => MessageBox.Show(this, t, "Kiln", MessageBoxButtons.OK, MessageBoxIcon.Information);
        if (_inst is null) return false;
        if (!_clientOk) { Msg("Choose your game folder first (step 1). The line under it must say \"Found\"."); return false; }
        if (_toStick.Checked && StickChoice() is null) { Msg("No memory stick is selected (step 3). Insert it and press Refresh, or choose a folder."); return false; }
        if (PcOut().Length == 0) { Msg(_toStick.Checked ? "Choose the data folder on this PC (Advanced settings)." : "Choose the folder for the data (step 3)."); return false; }
        if (WorkDir().Length == 0) { Msg("Choose a work folder (Advanced settings)."); return false; }
        foreach (var c in new[] { _game.Text.Trim() }.Concat(ExtraDirs()))
            if (ClientScan.Inside(PcOut(), c) || ClientScan.Inside(WorkDir(), c))
            { Msg("The data and work folders must be outside the game folders: Kiln never writes into a client."); return false; }
        if (ClientScan.Inside(PcOut(), _inst.Root) || ClientScan.Inside(WorkDir(), _inst.Root))
        { Msg("The data and work folders must be outside Kiln's own folder."); return false; }
        return true;
    }

    private bool SpaceOk()
    {
        var (text, low) = SpaceText();
        var stick = StickChoice();
        bool stickLow = stick is not null && stick.Free < Disk.DataBytes;
        if (!low && !stickLow) return true;
        string msg = (low ? text + "\n" : "") + (stickLow ? $"The memory stick has {Disk.Gb(stick!.Free)} free; the data needs about {Disk.Gb(Disk.DataBytes)}.\n" : "");
        return MessageBox.Show(this, "Low disk space:\n\n" + msg + "\nSteps that are already up to date need nothing. Start anyway?",
                               "Kiln", MessageBoxButtons.YesNo, MessageBoxIcon.Warning) == DialogResult.Yes;
    }

    /// Start (a run that resumes) or Update assets (--update, or a resume run when kiln.py has none),
    /// then, while kiln.py has no --target, the copy of the changed files to the memory stick.
    private async Task MakeAsync(KilnMode mode, ISet<string>? stickRepair = null)
    {
        if (_busy || !InputsOk()) return;
        var opt = Options(mode);
        if (opt.Steps.Count == 0) { MessageBox.Show(this, "Nothing is ticked under Advanced settings > What to make.", "Kiln"); return; }
        if (!SpaceOk()) return;
        if (StickData() is { } sd && Directory.Exists(sd) && Directory.EnumerateFileSystemEntries(sd).Any() &&
            !File.Exists(Path.Combine(sd, "kiln.copy.json")) &&
            MessageBox.Show(this, $"{sd} already holds a data folder that Kiln did not copy there.\n\nKiln replaces the files that differ " +
                                  "from what it makes (nothing is deleted). Continue?", "Kiln", MessageBoxButtons.YesNo, MessageBoxIcon.Warning) != DialogResult.Yes)
            return;
        if (mode == KilnMode.Update && !_caps.Has("--update"))
        {
            opt.Mode = KilnMode.Run;
            opt.Force = false;
            Log("note: this kiln.py has no --update: a resume run re-makes what changed (new converters, a changed client, damaged files).");
        }
        SaveSettings();
        BeginBusy(mode == KilnMode.Update ? "update" : "run");
        int rc = await RunKilnAsync(opt);
        bool copied = true;
        CardSync.CopyResult? copy = null;
        if (!_cancelled && StickChoice() is not null)
        {
            if (!_caps.Has("--target")) { if (rc == 0) (copied, copy) = await CopyToStickAsync(stickRepair); }
            else await CopyEbootAsync();                          // kiln.py copies data/; the EBOOT with menu art is ours
        }
        EndBusy();
        ShowRunSummary(mode, rc, copy, copied);
        await RunCheckAsync(keepStatus: true);
    }

    private async Task<int> RunKilnAsync(RunOptions opt)
    {
        var args = KilnCli.Args(opt, _inst!, _caps);
        Log("");
        Log($"== {opt.Mode.ToString().ToLowerInvariant()} {DateTime.Now:yyyy-MM-dd HH:mm:ss}");
        Log("$ " + KilnCli.Show(_inst!, args));
        bool makes = opt.Mode is KilnMode.Run or KilnMode.Update or KilnMode.Repair;
        if (makes)
        {
            foreach (var r in _steps.Rows.ToList())
            {
                if (r.Phase) continue;
                if (r.State is RowState.Planned or RowState.Cancelled or RowState.Failed || (r.State == RowState.UpToDate && opt.Force))
                { r.State = RowState.Queued; r.Seconds = 0; r.Started = null; r.Note = ""; }
                _steps.PaintRow(r);
            }
            foreach (var l in _kilnLocales)
                _steps.Upsert(new StepRow { Name = $"extract [{l}]", Group = "extract", Locale = l, Title = "client files this run needs, read from the archives", Phase = true, State = RowState.Queued, Weight = _eta.Get("extract:" + l) ?? EtaStore.Guess("extract", "") }, atTop: true);
            _tabs.SelectedIndex = 0;
        }
        _lastDone = null;
        _found.Clear();
        _extraFiles = 0;
        var proc = new KilnProcess();
        proc.Event += (kind, e) => BeginInvoke(() => OnEvent(kind, e));
        proc.Text += t => BeginInvoke(() => Log(t));
        int rc = await proc.RunAsync(_inst!, args, _runCts!.Token, _lowPrio.Checked ? ProcessPriorityClass.BelowNormal : ProcessPriorityClass.Normal);
        await Task.Delay(150);                                  // let queued BeginInvoke calls land
        foreach (var r in _steps.Rows.Where(r => r.State is RowState.Running or RowState.Queued).ToList())
        {
            if (r.State == RowState.Running) r.Seconds = r.Elapsed;
            r.State = _cancelled ? RowState.Cancelled : r.Phase ? RowState.Skipped : r.State == RowState.Running ? RowState.Failed : RowState.Queued;
            _steps.PaintRow(r);
        }
        _eta.Save();
        return rc;
    }

    private async Task<(bool ok, CardSync.CopyResult? r)> CopyToStickAsync(ISet<string>? only)
    {
        string src = DataDir(), dst = StickData()!, pcOut = PcOut(), gameDir = StickGameDir()!;
        _phaseText = "copying the changed files to the memory stick";
        _copyFraction = 0;
        Log($"== copy {src} -> {dst} (only files that differ)");
        try
        {
            var ct = _runCts!.Token;
            var r = await Task.Run(() =>
            {
                var res = CardSync.Copy(src, dst, t => BeginInvoke(() => Log(t)), p => _copyFraction = p, ct);
                if (only is { Count: > 0 })
                {
                    var rep = CardSync.Copy(src, dst, t => BeginInvoke(() => Log(t)), p => _copyFraction = p, ct, only);
                    res.Copied += rep.Copied; res.Bytes += rep.Bytes; res.Errors.AddRange(rep.Errors);
                }
                // the EBOOT with the menu art from this client, when Kiln made one
                string eb = Path.Combine(pcOut, "xmb", "EBOOT.PBP");
                string ebDst = Path.Combine(gameDir, "EBOOT.PBP");
                if (File.Exists(eb) && (!File.Exists(ebDst) || new FileInfo(ebDst).Length != new FileInfo(eb).Length ||
                                        File.GetLastWriteTimeUtc(ebDst) < File.GetLastWriteTimeUtc(eb).AddSeconds(-2)))
                {
                    File.Copy(eb, ebDst, true);
                    File.SetLastWriteTimeUtc(ebDst, File.GetLastWriteTimeUtc(eb));
                    BeginInvoke(() => Log($"copied EBOOT.PBP with your menu art -> {ebDst}"));
                }
                return res;
            }, ct);
            Log($"copy: {r.Copied} file(s) copied ({r.Bytes / 1048576.0:0.0} MB), {r.Same} already there" +
                (r.Errors.Count > 0 ? $", {r.Errors.Count} error(s)" : "") + (r.Extra.Count > 0 ? $"; {r.Extra.Count} other file(s) on the stick left alone" : ""));
            foreach (var e in r.Errors) Log("copy error: " + e);
            return (r.Errors.Count == 0, r);
        }
        catch (OperationCanceledException) { Log("copy cancelled"); return (false, null); }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException) { Log("copy failed: " + e.Message); return (false, null); }
        finally { _copyFraction = -1; _phaseText = ""; }
    }

    private async Task CheckIntegrityAsync()
    {
        if (_busy || !InputsOk()) return;
        if (!File.Exists(Path.Combine(DataDir(), "kiln.manifest.json")))
        {
            MessageBox.Show(this, $"There is no data made by Kiln in {DataDir()} yet: press Start first.", "Kiln", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }
        BeginBusy("check");
        _tabs.SelectedIndex = 1;
        var opt = Options(_caps.Has("--check") ? KilnMode.Check : KilnMode.Verify);
        int rc = await RunKilnAsync(opt);
        if (!_cancelled && StickChoice() is not null && !_caps.Has("--target") && Directory.Exists(DataDir()))
        {
            string src = DataDir(), dst = StickData()!;
            _phaseText = "checking the memory-stick copy (reads every file)";
            _copyFraction = 0;
            Log($"== check {dst} against {src}");
            try
            {
                var ct = _runCts!.Token;
                var bad = await Task.Run(() => CardSync.Check(src, dst, p => _copyFraction = p, ct), ct);
                foreach (var b in bad) { _found.Add(("memory stick", b.Path, b.What)); Log($"stick: {b.Path}: {b.What}"); }
                Log($"memory stick: {bad.Count} problem(s)");
            }
            catch (OperationCanceledException) { }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException) { _found.Add(("memory stick", dst, e.Message)); }
            finally { _copyFraction = -1; _phaseText = ""; }
        }
        EndBusy();
        ShowCheckSummary(rc);
    }

    /// Re-make what the check found bad on the PC (a resume run re-makes exactly the steps whose files
    /// do not match the manifest) and re-copy the bad files of the memory stick.
    private async Task RepairAsync()
    {
        if (_caps.Has("--repair")) { await MakeAsync(KilnMode.Repair); return; }
        var stickBad = _found.Where(x => x.where == "memory stick").Select(x => x.path.Replace('/', Path.DirectorySeparatorChar).Replace(Path.DirectorySeparatorChar, '/')).ToHashSet(StringComparer.OrdinalIgnoreCase);
        await MakeAsync(_caps.Has("--update") ? KilnMode.Update : KilnMode.Run, stickBad);
    }

    /// The EBOOT.PBP with menu art from this client (xmb/ of the output), copied next to data/ on the stick
    /// when it is new or changed. kiln.py's --target copies data/ only.
    private async Task CopyEbootAsync()
    {
        string eb = Path.Combine(PcOut(), "xmb", "EBOOT.PBP");
        string? dir = StickGameDir();
        if (dir is null || !File.Exists(eb)) return;
        string dst = Path.Combine(dir, "EBOOT.PBP");
        try
        {
            bool need = !File.Exists(dst) || new FileInfo(dst).Length != new FileInfo(eb).Length ||
                        File.GetLastWriteTimeUtc(dst) < File.GetLastWriteTimeUtc(eb).AddSeconds(-2);
            if (!need) return;
            await Task.Run(() => { Directory.CreateDirectory(dir); File.Copy(eb, dst, true); File.SetLastWriteTimeUtc(dst, File.GetLastWriteTimeUtc(eb)); });
            Log($"copied EBOOT.PBP with your menu art -> {dst}");
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException) { Log("EBOOT copy failed: " + e.Message); }
    }

    // ================================================================================== languages later
    /// A conversion that is already there: data/locales.txt with Kiln's manifest in the PC data folder, or
    /// data/locales.txt on the memory stick. CanChange: Kiln's manifest is where kiln.py --out points.
    private sealed record Existing(string Out, bool OnStick, bool CanChange, string Primary, List<string> Extra, string Start)
    {
        public List<string> All => new[] { Primary }.Concat(Extra).ToList();
    }

    private Existing? FindExisting()
    {
        string pcData = DataDir();
        if (pcData.Length > 0 && File.Exists(Path.Combine(pcData, "kiln.manifest.json")) && ClientScan.ReadLocales(pcData) is { } l)
            return new Existing(PcOut(), false, true, l.primary, l.extra, l.start);
        if (StickData() is { } sd && ClientScan.ReadLocales(sd) is { } k)
            return new Existing(StickGameDir()!, true, File.Exists(Path.Combine(sd, "kiln.manifest.json")), k.primary, k.extra, k.start);
        return null;
    }

    private void ShowExisting()
    {
        var ex = FindExisting();
        if (_existRow.Visible != (ex is not null))
        {
            _existRow.Visible = ex is not null;
            if (IsHandleCreated) BeginInvoke(FitSplit);
        }
        if (ex is null) return;
        _existInfo.Text = $"Existing conversion{(ex.OnStick ? " on the memory stick" : "")}: languages {string.Join(", ", ex.All)}, starts in {ex.Start}.";
        bool mode = _caps.Has("--remove-locale");
        _addLang.Enabled = !_busy && _ready && ex.CanChange && mode;
        _removeLang.Enabled = _addLang.Enabled && ex.Extra.Count > 0;
        string why = !mode ? "This kiln.py cannot change the languages of an existing conversion yet."
                   : !ex.CanChange ? "Kiln's data folder on this PC (Advanced settings) is needed to change the languages."
                   : "";
        _tips.SetToolTip(_addLang, why.Length > 0 ? why : "Add the language of another copy of the client (same build) to this conversion. Step 1 is not needed.");
        _tips.SetToolTip(_removeLang, why.Length > 0 ? why : "Remove one of the extra languages from this conversion.");
    }

    private async Task AddLanguageAsync()
    {
        var ex = FindExisting();
        if (ex is null || _busy) return;
        using var dlg = new FolderBrowserDialog { Description = "The copy of the client in the language to add (same build)", UseDescriptionForTitle = true };
        if (dlg.ShowDialog(this) != DialogResult.OK) return;
        string d = dlg.SelectedPath;
        var locs = ClientScan.Locales(d);
        var add = locs.Except(ex.All).ToList();
        if (locs.Count == 0) { MessageBox.Show(this, "No language archive in that folder (Data, a xxYY folder with locale-xxYY.MPQ).", "Kiln"); return; }
        if (add.Count == 0) { MessageBox.Show(this, $"The game already has {string.Join(", ", locs)}.", "Kiln"); return; }
        if (!LanguagePathsOk(ex, d)) return;
        var opt = new RunOptions
        {
            Mode = KilnMode.Run, Game = "", Out = ex.Out, Work = WorkDir(), AddLocales = new() { d }, Jobs = (int)_jobs.Value, App = AppName(),
            Target = !ex.OnStick && StickChoice() is not null ? StickGameDir() : null,
        };
        await LanguageRunAsync(ex, opt, "add " + string.Join(", ", add));
    }

    private async Task RemoveLanguageAsync()
    {
        var ex = FindExisting();
        if (ex is null || _busy || ex.Extra.Count == 0) return;
        string? loc = ex.Extra.Count == 1
            ? (MessageBox.Show(this, $"Remove {ex.Extra[0]} from the game? (The main language {ex.Primary} stays.)", "Kiln",
                               MessageBoxButtons.YesNo, MessageBoxIcon.Question) == DialogResult.Yes ? ex.Extra[0] : null)
            : PickLanguage(ex.Extra);
        if (loc is null || !LanguagePathsOk(ex, null)) return;
        var opt = new RunOptions
        {
            Mode = KilnMode.Run, Game = "", Out = ex.Out, Work = WorkDir(), RemoveLocale = loc, Jobs = (int)_jobs.Value, App = AppName(),
            Target = !ex.OnStick && StickChoice() is not null ? StickGameDir() : null,
        };
        await LanguageRunAsync(ex, opt, "remove " + loc);
    }

    private bool LanguagePathsOk(Existing ex, string? client)
    {
        if (_inst is null) return false;
        if (WorkDir().Length == 0) { MessageBox.Show(this, "Choose a work folder (Advanced settings).", "Kiln"); return false; }
        if (client is not null && (ClientScan.Inside(ex.Out, client) || ClientScan.Inside(WorkDir(), client)))
        { MessageBox.Show(this, "The data and work folders must be outside the client folder.", "Kiln"); return false; }
        return true;
    }

    private string? PickLanguage(List<string> choices)
    {
        using var f = new Form
        {
            Text = "Remove a language", FormBorderStyle = FormBorderStyle.FixedDialog, MinimizeBox = false, MaximizeBox = false,
            StartPosition = FormStartPosition.CenterParent, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, Font = Font,
            Padding = new Padding(Dev(10)),
        };
        var combo = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Width = Dev(100) };
        combo.Items.AddRange(choices.Cast<object>().ToArray());
        combo.SelectedIndex = 0;
        var ok = new Button { Text = "Remove", DialogResult = DialogResult.OK, AutoSize = true };
        var cancel = new Button { Text = "Cancel", DialogResult = DialogResult.Cancel, AutoSize = true };
        var row = new FlowLayoutPanel { AutoSize = true, Dock = DockStyle.Fill };
        row.Controls.AddRange(new Control[] { new Label { Text = "Language:", AutoSize = true, Margin = new Padding(0, Dev(6), Dev(4), 0) }, combo, ok, cancel });
        f.Controls.Add(row);
        f.AcceptButton = ok;
        f.CancelButton = cancel;
        return f.ShowDialog(this) == DialogResult.OK ? combo.SelectedItem as string : null;
    }

    private async Task LanguageRunAsync(Existing before, RunOptions opt, string what)
    {
        SaveSettings();
        BeginBusy("run");
        Log($"== languages: {what} ({before.Out})");
        int rc = await RunKilnAsync(opt);
        bool copied = true;
        CardSync.CopyResult? copy = null;
        if (!_cancelled && rc == 0 && !before.OnStick && StickChoice() is not null && !_caps.Has("--target"))
            (copied, copy) = await CopyToStickAsync(null);
        EndBusy();
        var after = FindExisting();
        string title = _cancelled ? "Cancelled." : rc != 0 ? $"Changing the languages did not finish (exit code {rc}); the Log tab says why." :
                       !copied ? "The languages changed on this PC, but the copy to the memory stick failed (Log tab)." : "Languages changed.";
        Color color = _cancelled ? Warn : rc != 0 || !copied ? Bad : Good;
        var lines = new List<string>();
        if (after is not null)
        {
            lines.Add($"Languages now in the game: {string.Join(", ", after.All)}. The game starts in {after.Start}.");
            lines.Add("Players switch the language in the game's main menu or settings.");
        }
        lines.Add($"Time: {StepList.Human((DateTime.UtcNow - _runStart).TotalSeconds)}.");
        if (StickChoice() is not null && !before.OnStick)
            lines.Add(copy is not null ? $"Memory stick: {copy.Copied} file(s) copied." : $"Memory stick: {StickData()}.");
        SetSummary(title, color, lines, null);
        SetStatus(title, color);
        _tabs.SelectedIndex = 2;
        ShowExisting();
        await RunCheckAsync(keepStatus: true);
    }

    private void BeginBusy(string what)
    {
        _busy = true;
        _cancelled = false;
        _checkCts?.Cancel();
        _runCts = new CancellationTokenSource();
        _runStart = DateTime.UtcNow;
        _overall.Value = 0;
        _tick.Start();
        SetStatus(what switch { "check" => "Checking...", "update" => "Updating...", _ => "Working..." }, null);
        UpdateButtons();
    }

    private void EndBusy()
    {
        _tick.Stop();
        _busy = false;
        _runCts?.Dispose();
        _runCts = null;
        Text = "Kiln " + _inst?.Version;
        UpdateButtons();
    }

    private void CancelRun()
    {
        if (!_busy || _runCts is null) return;
        _cancelled = true;
        _cancel.Enabled = false;
        SetStatus("Cancelling (stopping kiln.py and the converter it runs)...", null);
        Log("== cancel requested");
        _runCts.Cancel();
    }

    // ================================================================================== summary
    private void ShowRunSummary(KilnMode mode, int rc, CardSync.CopyResult? copy, bool copied)
    {
        double secs = (DateTime.UtcNow - _runStart).TotalSeconds;  // the whole run, the memory stick copy included
        var (files, bytes) = Directory.Exists(DataDir()) ? CardSync.Measure(DataDir()) : (0, 0L);
        var lines = new List<string>();
        string title;
        Color color;
        if (_cancelled) { title = "Cancelled. Start resumes: finished steps are kept."; color = Warn; }
        else if (rc != 0) { title = $"Finished with problems (exit code {rc}). Start again retries what failed; the Log tab says why."; color = Bad; }
        else if (!copied) { title = "The data is made, but copying it to the memory stick failed (see the Log tab)."; color = Bad; }
        else { title = mode == KilnMode.Update ? "Update finished." : "Done."; color = Good; }
        if (!_cancelled) title += $" Total time: {LongTime(secs)}.";
        string breakdown = "";
        if (_lastDone?.Obj("spent") is { } sp)
            breakdown = $" - extraction {LongTime(sp.Num("extract") ?? 0)}, PNG {LongTime(sp.Num("png") ?? 0)}, steps {LongTime(sp.Num("steps") ?? 0)}" +
                        (secs - (_lastDone?.Num("seconds") ?? secs) > 30 ? $", the rest (checks, copying to the stick) {LongTime(secs - (_lastDone?.Num("seconds") ?? secs))}" : "");
        lines.Add($"Total time: {LongTime(secs)} (started {_runStart.ToLocalTime():HH:mm}, finished {DateTime.Now:HH:mm}){breakdown}.");
        Log($"total time: {LongTime(secs)}");
        if (_lastDone is { } d && d.Bool("verify") != true)
        {
            var failed = d.Strs("failed").ToList();
            lines.Add($"Steps: {d.Num("made") ?? 0} made, {d.Num("skipped") ?? 0} already up to date, {failed.Count} failed{(failed.Count > 0 ? " (" + string.Join(", ", failed) + ")" : "")}.");
            var big = d.Strs("too_big_for_fat32").ToList();
            if (big.Count > 0) lines.Add("Warning: files of 4 GB or more do not fit a FAT32 memory stick: " + string.Join(", ", big));
        }
        lines.Add($"Data on this PC: {DataDir()} ({files} files, {Disk.Gb(bytes)}).");
        if (StickChoice() is not null)
        {
            if (copy is not null) lines.Add($"Memory stick: {StickData()} ({copy.Copied} file(s) copied, {copy.Same} already there).");
            else if (_lastDone?.Obj("copy") is { } cp)
                lines.Add($"Memory stick: {StickData()} ({cp.Num("copied") ?? 0} file(s) copied, {cp.Num("kept") ?? 0} already there" +
                          ((cp.Num("failed") ?? 0) > 0 ? $", {cp.Num("failed")} failed" : "") + ").");
            lines.Add("Next: put the port's EBOOT.PBP into " + StickGameDir() + " if it is not there yet, and start the game on the PSP.");
        }
        else lines.Add($"Next: copy {DataDir()} to the memory stick as PSP/GAME/{AppName()}/data, next to the port's EBOOT.PBP.");
        string eb = Path.Combine(PcOut(), "xmb", "EBOOT.PBP");
        if (File.Exists(eb)) lines.Add($"Your EBOOT with menu art from your client: {eb}" + (StickChoice() is not null ? " (also copied to the stick)." : "."));
        if (FindExisting() is { } ex && ex.Extra.Count > 0)
            lines.Add($"Languages in the game: {string.Join(", ", ex.All)} (starts in {ex.Start}); players switch the language in the game's main menu or settings.");
        lines.Add("The work folder (" + WorkDir() + ") holds files extracted from your client: keep it for faster updates, delete it when you no longer need it.");
        SetSummary(title, color, lines, null);
        SetStatus(title, color);
        if (!_cancelled) _tabs.SelectedIndex = 2;
    }

    // 13976 s -> "3 h 52 min 56 s" (kiln.py human_time)
    private static string LongTime(double seconds)
    {
        var t = TimeSpan.FromSeconds(Math.Round(Math.Max(0, seconds)));
        if (t.TotalHours >= 1) return $"{(int)t.TotalHours} h {t.Minutes:00} min {t.Seconds:00} s";
        if (t.TotalMinutes >= 1) return $"{t.Minutes} min {t.Seconds:00} s";
        return $"{t.Seconds} s";
    }

    private void ShowCheckSummary(int rc)
    {
        var lines = new List<string>();
        int n = _found.Count;
        string title;
        Color color;
        if (_cancelled) { title = "Check cancelled."; color = Warn; }
        else if (n == 0 && rc == 0) { title = "Integrity check: everything matches Kiln's manifest."; color = Good; }
        else { title = $"Integrity check: {Math.Max(n, 1)} problem(s). Repair re-makes or re-copies only the bad files."; color = Bad; }
        if (_lastDone is { } d && d.Bool("check") == true)
            lines.Add($"Checked {d.Num("files") ?? 0} files ({Disk.Gb((long)(d.Num("bytes") ?? 0))}) in {StepList.Human(d.Num("seconds") ?? 0)}.");
        lines.Add($"Data on this PC: {DataDir()}.");
        if (StickChoice() is not null) lines.Add($"Memory stick: {StickData()}{(_caps.Has("--target") ? "" : " (compared with the PC copy by this window)")}.");
        if (_extraFiles > 0) lines.Add($"{_extraFiles} file(s) that no step made were found and left alone (not a problem).");
        if (n == 0 && rc != 0) lines.Add($"kiln.py reported a problem (exit code {rc}); see the Log tab.");
        SetSummary(title, color, lines, _found);
        SetStatus(title, color);
        _repair.Enabled = !_cancelled && (n > 0 || rc != 0);
        _tabs.SelectedIndex = 2;
    }

    private void SetSummary(string title, Color color, List<string> lines, List<(string where, string path, string what)>? problems)
    {
        _sumTitle.Text = title;
        _sumTitle.ForeColor = color;
        _sumText.Text = string.Join(Environment.NewLine, lines);
        _problems.BeginUpdate();
        _problems.Items.Clear();
        foreach (var p in (problems ?? new()).Take(500))
            _problems.Items.Add(new ListViewItem(new[] { p.where, p.path, p.what }));
        _problems.EndUpdate();
        _problems.Visible = _problems.Items.Count > 0;
        _sumOpenStick.Visible = StickChoice() is not null;
    }

    // ================================================================================== events
    private void OnEvent(string kind, JsonElement e)
    {
        switch (kind)
        {
            case "log": Log(e.Str("text") ?? ""); break;
            case "client": ApplyClient(e); break;
            case "error":
                Log("error: " + (e.Str("message") ?? ""));
                foreach (var m in e.Strs("missing")) Log("   missing: " + m);
                SetStatus("Error: " + (e.Str("message") ?? "").Split('\n')[0], Bad);
                break;
            case "plan":
                ShowPlan(e, running: true);
                foreach (var r in _steps.Rows.Where(r => r.Phase && r.State == RowState.Queued && r.Name.StartsWith("extract", StringComparison.Ordinal)).ToList())
                { r.State = RowState.Skipped; _steps.PaintRow(r); }
                break;
            case "phase": OnPhase(e); break;
            case "step": OnStep(e); break;
            case "running":
                if (e.Obj("steps") is { } st)
                    foreach (var p in st.EnumerateObject())
                    {
                        string n = p.Name.StartsWith("extract:", StringComparison.Ordinal) ? $"extract [{p.Name[8..]}]" : p.Name;
                        if (_steps.Get(n) is { State: RowState.Running } r && p.Value.ValueKind == JsonValueKind.Number)
                            r.Started = DateTime.UtcNow - TimeSpan.FromSeconds(p.Value.GetDouble());
                    }
                break;
            case "done":
                _lastDone = e;
                if (e.Bool("verify") == true) Log($"verify: {e.Num("problems") ?? 0} file problem(s), {e.Num("failed_verify") ?? 0} failed verify pass(es)");
                else if (e.Str("data") is { } data) Log($"done: data in {data}");
                break;
            case "check":
            {
                string where = e.Str("where") == "target" ? "memory stick" : "this PC";
                string kind2 = e.Str("problem") ?? "";
                string path = e.Str("path") is { Length: > 0 } pp ? pp : e.Str("step") ?? "";
                string what = kind2 + (e.Str("detail") is { Length: > 0 } dt ? ": " + dt : "");
                if (kind2 == "extra") { _extraFiles++; Log($"check: {where}: {path}: extra file (left alone)"); break; }
                _found.Add((where, path, what));
                Log($"check: {where}: {path}: {what}");
                break;
            }
            case "copy":
                _phaseText = "copying to the memory stick: " + e.Str("path");
                Log($"copied {e.Str("path")} ({(e.Num("bytes") ?? 0) / 1048576.0:0.0} MB)");
                break;
            case "update":
                Log($"update: {e.Str("step")}: {e.Str("reason")}");
                break;
            default:
                // integrity findings (--verify's 'verify' events, --check's events): anything with a problem
                if (e.Str("problem") is { } problem)
                {
                    string where = e.Str("where") ?? e.Str("location") ?? (e.Str("target") is not null ? "memory stick" : "this PC");
                    string path = e.Str("path") ?? e.Str("step") ?? "";
                    _found.Add((where, path, problem));
                    Log($"{kind}: {where}: {path}: {problem}");
                }
                break;
        }
    }

    private void OnPhase(JsonElement e)
    {
        string phase = e.Str("phase") ?? "";
        string state = e.Str("state") ?? "";
        string name = phase == "extract" ? $"extract [{e.Str("locale")}]" : phase;
        if (phase == "probe") { _phaseText = "listing client files the archives do not name: " + e.Str("step"); return; }
        if (phase != "extract" && phase != "png") { if (state == "start") _phaseText = phase; else if (state == "end") _phaseText = ""; return; }
        var row = _steps.Get(name) ?? _steps.Upsert(new StepRow
        {
            Name = name, Group = phase, Locale = e.Str("locale") ?? "", Phase = true, State = RowState.Queued,
            Title = phase == "png" ? "interface art as PNG side files for the Python bakers" : "client files this run needs",
            Weight = _eta.Get(phase == "png" ? "png" : "extract:" + e.Str("locale")) ?? EtaStore.Guess(name, ""),
        }, atTop: true);
        if (state == "start")
        {
            row.State = RowState.Running;
            row.Started = DateTime.UtcNow;
            _phaseText = phase == "png" ? "making PNG side files of the interface art" : $"extracting client files [{e.Str("locale")}]";
        }
        else if (state == "end")
        {
            row.Seconds = e.Num("seconds") ?? row.Elapsed;
            int bad = (int)(e.Num("rc") ?? 0) + (int)(e.Num("failed") ?? 0);
            row.State = bad == 0 ? RowState.Done : RowState.Failed;
            if (phase == "png") row.Note = $"{e.Num("written") ?? 0} written, {e.Num("kept") ?? 0} kept, {e.Num("failed") ?? 0} failed" +
                                           ((e.Num("empty") ?? 0) > 0 ? $", {e.Num("empty")} empty in the client (skipped)" : "");
            if (row.Seconds > 0) _eta.Record(phase == "png" ? "png" : "extract:" + e.Str("locale"), row.Seconds);
            _phaseText = "";
        }
        _steps.PaintRow(row);
    }

    private void OnStep(JsonElement e)
    {
        string name = e.Str("name") ?? "?";
        string state = e.Str("state") ?? "";
        var row = _steps.Get(name) ?? _steps.Upsert(new StepRow
        {
            Name = name, Title = e.Str("title") ?? "", State = RowState.Queued, Weight = _eta.Get(name) ?? EtaStore.Guess(name, ""),
        });
        foreach (var ph in _steps.Rows.Where(r => r.Phase && r.State == RowState.Queued).ToList())
        { ph.State = RowState.Skipped; _steps.PaintRow(ph); }
        switch (state)
        {
            case "start":
                row.State = RowState.Running;
                row.Started = DateTime.UtcNow;
                if (e.Str("title") is { Length: > 0 } t) row.Title = t;
                _steps.EnsureVisibleRow(row);
                break;
            case "skipped":
                row.State = RowState.Skipped;
                break;
            case "ok":
            case "failed":
                row.State = state == "ok" ? RowState.Done : RowState.Failed;
                row.Seconds = e.Num("seconds") ?? row.Elapsed;
                row.Files = (int)(e.Num("files") ?? 0);
                row.Bytes = (long)(e.Num("bytes") ?? 0);
                row.Note = e.Str("note") ?? "";
                if (state == "ok") _eta.Record(name, row.Seconds);
                break;
        }
        _steps.PaintRow(row);
    }

    private void OnTick()
    {
        if (!_busy) return;
        _steps.PaintRunning();
        double total = 0, done = 0;
        foreach (var r in _steps.Rows)
        {
            if (r.State is RowState.UpToDate or RowState.Skipped or RowState.Missing or RowState.Cancelled or RowState.Planned) continue;
            double w = Math.Max(1, r.Weight);
            total += w;
            done += w * r.Fraction;
        }
        double p = _copyFraction >= 0 ? _copyFraction : total > 0 ? done / total : 0;
        _overall.Value = Math.Clamp((int)(p * _overall.Maximum), 0, _overall.Maximum);
        double el = (DateTime.UtcNow - _runStart).TotalSeconds;
        string eta = p > 0.03 && el > 20 ? $", about {StepList.Human(el * (1 - p) / p)} left" : "";
        Text = $"Kiln {_inst?.Version} - {p * 100:0}%";
        var running = _steps.Rows.Where(r => r.State == RowState.Running && !r.Phase).Select(r => r.Name).ToList();
        string what = _phaseText.Length > 0 ? _phaseText : running.Count > 0 ? "running: " + string.Join(", ", running.Take(5)) + (running.Count > 5 ? $" and {running.Count - 5} more" : "") : "working";
        SetStatus($"{p * 100:0}%  -  {StepList.Human(el)} elapsed{eta}  -  {what}", null);
    }

    // ================================================================================== misc
    private void UpdateButtons()
    {
        bool idle = !_busy && _inst is not null && _ready;
        foreach (Control c in new Control[] { _game, _folder, _folderBrowse, _stick, _stickRefresh, _toStick, _toFolder, _work, _pcOut, _eboot, _server, _app, _groups, _jobs, _force, _lowPrio })
            c.Enabled = idle;
        bool locales = _caps.Has("--add-locale");
        _extras.Enabled = _addExtra.Enabled = _removeExtra.Enabled = _defaultLocale.Enabled = idle && locales;
        if (!_caps.Has("--eboot")) _eboot.Enabled = false;
        if (idle && _stick.SelectedItem is not Hardware.Stick) _toStick.Enabled = false;
        bool hasData = File.Exists(Path.Combine(DataDir(), "kiln.manifest.json"));
        _start.Enabled = idle && _clientOk;
        _update.Enabled = idle && _clientOk && hasData;
        _checkInt.Enabled = idle && _clientOk && hasData;
        if (!idle) _repair.Enabled = false;
        _cancel.Enabled = _busy;
        ShowExisting();
        bool partial = _steps.Rows.Any(r => r.State == RowState.UpToDate) && _steps.Rows.Any(r => r.State == RowState.Planned);
        _start.Text = _busy ? "Working..." : (hasData && partial) || _cancelled ? "Resume" : "Start";
        _tips.SetToolTip(_update, hasData ? "Re-make only what changed (new Kiln or converters, a changed client) and copy only the changed files to the memory stick."
                                          : "Available after the first run.");
    }

    private void SetGameInfo(string text, bool? ok)
    {
        _gameInfo.Text = text;
        _gameInfo.ForeColor = ok switch { true => Good, false => Bad, _ => HelpColor };
    }

    private void SetStatus(string text, Color? color)
    {
        _status.Text = text;
        _status.ForeColor = color ?? SystemColors.ControlText;
        if (!_busy) _tips.SetToolTip(_status, text);
    }

    private void Log(string line)
    {
        if (_log.TextLength > 4_000_000) _log.Text = _log.Text[^2_000_000..];
        _log.AppendText(line + Environment.NewLine);
    }

    private void OpenFolder(string dir)
    {
        if (dir.Length == 0 || !Directory.Exists(dir))
        {
            MessageBox.Show(this, dir.Length == 0 ? "Nothing chosen yet." : $"{dir} does not exist yet: a run creates it.", "Kiln", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }
        try { Process.Start(new ProcessStartInfo("explorer.exe") { ArgumentList = { dir }, UseShellExecute = false }); }
        catch (Exception ex) { MessageBox.Show(this, dir + "\n\n" + ex.Message, "Kiln"); }
    }

    private void OpenNote()
    {
        string note = Path.Combine(PcOut(), "README_PERSONAL_USE.txt");
        if (PcOut().Length > 0 && File.Exists(note))
        {
            try { Process.Start(new ProcessStartInfo(note) { UseShellExecute = true }); return; } catch { }
        }
        MessageBox.Show(this,
            "Everything under data and xmb is converted from YOUR OWN copy of the game, for YOUR OWN use on your own device. " +
            "Do not share, upload or sell these files, or any archive, image or build that contains them. The work folder holds " +
            "files extracted from your client: delete it when you no longer need it.\n\nKiln writes this note as README_PERSONAL_USE.txt " +
            "next to the data when a run starts.",
            "Personal use only", MessageBoxButtons.OK, MessageBoxIcon.Information);
    }

    private void OpenStepLog()
    {
        if (_steps.SelectedItems.Count == 0 || _steps.SelectedItems[0].Tag is not StepRow r) return;
        string name = r.Phase && r.Name.StartsWith("extract [", StringComparison.Ordinal) ? "extract_" + r.Locale : r.Phase ? "kiln" : r.Name;
        string log = Path.Combine(PcOut(), "logs", name + ".log");
        if (!File.Exists(log)) { SetStatus("No log yet: " + log, null); return; }
        try { Process.Start(new ProcessStartInfo(log) { UseShellExecute = true }); }
        catch (Exception ex) { SetStatus(ex.Message, Bad); }
    }

    private void CopyCommand()
    {
        if (_inst is null) return;
        string cmd = KilnCli.Show(_inst, KilnCli.Args(Options(KilnMode.Run), _inst, _caps));
        try { Clipboard.SetText(cmd); SetStatus("The command line is on the clipboard.", null); } catch { }
        Log("$ " + cmd);
    }

    private void OnClosing(object? sender, FormClosingEventArgs e)
    {
        if (_busy)
        {
            if (MessageBox.Show(this, "Kiln is working. Stop it and close? (Start resumes later: finished steps are kept.)", "Kiln",
                                MessageBoxButtons.YesNo, MessageBoxIcon.Warning) != DialogResult.Yes)
            {
                e.Cancel = true;
                return;
            }
            _cancelled = true;
            _runCts?.Cancel();
        }
        _checkCts?.Cancel();
        if (_ready) SaveSettings();
        _eta.Save();
    }
}
