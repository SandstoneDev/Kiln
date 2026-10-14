// The back end: finds kiln.py and a Python to run it with, learns which options this kiln.py has,
// builds the command line from the form's choices and runs it, turning the JSON progress stream
// (one object per line on stdout) into events. Kiln's own logic stays in kiln.py; this side only
// starts, watches and stops it.
using System.Diagnostics;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace Kiln;

public sealed class KilnInstall
{
    public string Root { get; init; } = "";          // folder with kiln.py and kilnlib/
    public string Python { get; init; } = "";        // python.exe (embedded) or "python"/"py" on PATH
    public bool Bundled { get; init; }               // python\python.exe next to Kiln.exe
    public string Version { get; init; } = "?";      // KILN_VERSION from kilnlib/__init__.py
    public string? ToolsOverride { get; init; }      // dev tree: build/converters when bin/ is empty
    public string? DllDir { get; init; }             // dev tree: the MSYS2 UCRT64 runtime of those converters

    public string Script => Path.Combine(Root, "kiln.py");

    /// The bundle layout first (kiln.py and python\ next to Kiln.exe), then a development tree
    /// (kiln.py in a parent folder of gui\bin\...). Null when no kiln.py is found.
    public static KilnInstall? Find()
    {
        string exeDir = AppContext.BaseDirectory;
        string? root = null;
        for (var d = new DirectoryInfo(exeDir); d is not null; d = d.Parent)
        {
            if (File.Exists(Path.Combine(d.FullName, "kiln.py")) && Directory.Exists(Path.Combine(d.FullName, "kilnlib")))
            {
                root = d.FullName;
                break;
            }
        }
        if (root is null) return null;

        string embedded = Path.Combine(root, "python", "python.exe");
        bool bundled = File.Exists(embedded);
        string python = bundled ? embedded : FindSystemPython() ?? "python";

        string version = "?";
        try
        {
            var m = Regex.Match(File.ReadAllText(Path.Combine(root, "kilnlib", "__init__.py")),
                                @"KILN_VERSION\s*=\s*""([^""]+)""");
            if (m.Success) version = m.Groups[1].Value;
        }
        catch (IOException) { }

        string? tools = null;
        string extractor = "psp_mpq_extract.exe";
        if (!File.Exists(Path.Combine(root, "bin", extractor)) &&
            File.Exists(Path.Combine(root, "build", "converters", extractor)))
            tools = Path.Combine(root, "build", "converters");

        // A development tree's converters import the MSYS2 UCRT64 runtime DLLs (the bundle has them in bin/).
        string? dlls = null;
        if (tools is not null && string.IsNullOrEmpty(Environment.GetEnvironmentVariable("KILN_DLL_DIR")))
        {
            string ucrt = Path.Combine(Path.GetPathRoot(Environment.SystemDirectory) ?? "", "msys64", "ucrt64", "bin");
            if (Directory.Exists(ucrt)) dlls = ucrt;
        }
        return new KilnInstall { Root = root, Python = python, Bundled = bundled, Version = version, ToolsOverride = tools, DllDir = dlls };
    }

    private static string? FindSystemPython()
    {
        foreach (var exe in new[] { "python", "py" })
        {
            try
            {
                var psi = new ProcessStartInfo(exe) { UseShellExecute = false, CreateNoWindow = true,
                                                      RedirectStandardOutput = true, RedirectStandardError = true };
                psi.ArgumentList.Add("--version");
                using var p = Process.Start(psi);
                if (p is null) continue;
                p.WaitForExit(5000);
                if (p.HasExited && p.ExitCode == 0) return exe;
            }
            catch (System.ComponentModel.Win32Exception) { }
        }
        return null;
    }
}

/// What a kiln.py call does.
public enum KilnMode
{
    DryRun,     // --dry-run: identify the client and plan; writes nothing
    Run,        // a run (resumes: finished steps are skipped)
    Update,     // --update: re-make only what changed and refresh the memory-stick copy
    Check,      // --check: verify the output (and the stick copy with --target) against the manifest
    Verify,     // --verify: the older integrity pass (output folder only)
    Repair,     // --repair: check, re-make the steps with bad files, re-copy the bad files of the stick
}

/// What the user chose in the form, as kiln.py options.
public sealed class RunOptions
{
    public KilnMode Mode = KilnMode.Run;
    public string Game = "";
    public string Out = "";
    public string? Work;
    public string? Target;                         // the game folder on the memory stick (PSP/GAME/<APP>); kiln.py --target
    public List<string> AddLocales = new();
    public string? DefaultLocale;
    public string? RemoveLocale;                   // kiln.py --remove-locale xxYY
    public List<string> Steps = new();             // empty = Kiln's default
    public int Jobs = Environment.ProcessorCount;
    public bool Force;
    public string? Eboot;
    public string App = "WOWEEPSP";
}

/// The options kiln.py accepts (read from its --help), so the form can switch off what an older or
/// newer kiln.py does not have instead of failing with a usage error.
public sealed class KilnCaps
{
    public HashSet<string> Options { get; } = new(StringComparer.Ordinal);
    public bool Has(string opt) => Options.Contains(opt);
}

/// One step group of the step table (a checkbox in the form).
public sealed record StepGroup(string Id, string Label, bool Default, bool IsMapGroup, IReadOnlyList<string> Steps);

public static class KilnCli
{
    /// The kiln.py arguments for a run. Options this kiln.py does not know are left out (the form has
    /// already greyed out their controls and said why).
    public static List<string> Args(RunOptions o, KilnInstall inst, KilnCaps caps)
    {
        var a = new List<string>();
        if (o.Game.Length > 0) { a.Add("--game"); a.Add(o.Game); }      // a language added to an existing conversion needs no --game
        a.AddRange(new[] { "--out", o.Out, "--json" });
        switch (o.Mode)
        {
            case KilnMode.DryRun: a.Add("--dry-run"); break;
            case KilnMode.Verify: a.Add("--verify"); break;
            case KilnMode.Check: a.Add("--check"); break;
            case KilnMode.Update: a.Add("--update"); break;
            case KilnMode.Repair: a.Add("--repair"); break;
        }
        bool makes = o.Mode is KilnMode.Run or KilnMode.Update or KilnMode.Repair or KilnMode.DryRun;
        if (!string.IsNullOrWhiteSpace(o.Work)) { a.Add("--work"); a.Add(o.Work!); }
        if (!string.IsNullOrWhiteSpace(o.Target) && caps.Has("--target") && o.Mode != KilnMode.DryRun) { a.Add("--target"); a.Add(o.Target!); }
        if (caps.Has("--add-locale"))
            foreach (var d in o.AddLocales) { a.Add("--add-locale"); a.Add(d); }
        if (caps.Has("--default-locale") && !string.IsNullOrEmpty(o.DefaultLocale)) { a.Add("--default-locale"); a.Add(o.DefaultLocale!); }
        if (!string.IsNullOrEmpty(o.RemoveLocale)) { a.Add("--remove-locale"); a.Add(o.RemoveLocale!); }
        if (makes && o.Steps.Count > 0) { a.Add("--steps"); a.Add(string.Join(",", o.Steps)); }
        a.Add("--jobs"); a.Add(Math.Max(1, o.Jobs).ToString());
        if (o.Force && o.Mode == KilnMode.Run) a.Add("--force");
        if (makes && !string.IsNullOrWhiteSpace(o.Eboot) && caps.Has("--eboot")) { a.Add("--eboot"); a.Add(o.Eboot!); }
        if (!string.IsNullOrWhiteSpace(o.App) && o.App != "WOWEEPSP") { a.Add("--app"); a.Add(o.App); }
        if (inst.ToolsOverride is not null) { a.Add("--tools"); a.Add(inst.ToolsOverride); }
        if (inst.DllDir is not null) { a.Add("--dll-dir"); a.Add(inst.DllDir); }
        return a;
    }

    /// The command line as text (shown in the log and copied by "Copy command").
    public static string Show(KilnInstall inst, IEnumerable<string> args)
    {
        static string Q(string s) => s.Length > 0 && s.IndexOfAny(new[] { ' ', '\t', '"' }) < 0 ? s : "\"" + s.Replace("\"", "\\\"") + "\"";
        return string.Join(" ", new[] { Q(inst.Python), Q(inst.Script) }.Concat(args.Select(Q)));
    }

    /// kiln.py --help: the option names it accepts.
    public static async Task<KilnCaps> ReadCapsAsync(KilnInstall inst)
    {
        var caps = new KilnCaps();
        var r = await RunTextAsync(inst, new[] { "--help" }, TimeSpan.FromSeconds(30));
        foreach (Match m in Regex.Matches(r.Output, @"(?<![\w-])--[a-z][a-z0-9-]*"))
            caps.Options.Add(m.Value);
        return caps;
    }

    /// kiln.py --list-steps: the step groups. The table is text, so this reads the rows between the
    /// header and the first blank line (name, group, locale, title) and the "map groups:" line.
    /// Falls back to the groups Kiln 0.1 has when the table cannot be read.
    public static async Task<List<StepGroup>> ReadGroupsAsync(KilnInstall inst)
    {
        var r = await RunTextAsync(inst, new[] { "--list-steps" }, TimeSpan.FromSeconds(30));
        var groups = ParseGroups(r.Output);
        return groups.Count > 0 ? groups : FallbackGroups();
    }

    public static List<StepGroup> ParseGroups(string text)
    {
        var byGroup = new Dictionary<string, List<string>>(StringComparer.Ordinal);
        var order = new List<string>();
        bool inTable = false;
        string[] mapGroups = Array.Empty<string>(), mapDefault = Array.Empty<string>();
        foreach (var raw in text.Replace("\r", "").Split('\n'))
        {
            var line = raw.TrimEnd();
            if (Regex.IsMatch(line, @"^step\s+group\s+locale\b")) { inTable = true; continue; }
            var mg = Regex.Match(line, @"^map groups:\s*(.*?)\s*\(default:\s*(.*?)\)\s*$");
            if (mg.Success)
            {
                mapGroups = mg.Groups[1].Value.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
                mapDefault = mg.Groups[2].Value.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
                continue;
            }
            if (!inTable) continue;
            if (line.Length == 0) { inTable = false; continue; }             // the table ends at the first blank line
            if (char.IsWhiteSpace(raw[0])) continue;                        // detail lines of a step
            var m = Regex.Match(line, @"^([A-Za-z0-9_@*\-]+)\s+([a-z0-9_]+)\s+(\S+)\s+(.*)$");
            if (!m.Success) continue;
            string name = m.Groups[1].Value, group = m.Groups[2].Value;
            if (group == "maps") continue;                                  // per-map rows: the map groups below
            if (!byGroup.TryGetValue(group, out var list)) { list = new(); byGroup[group] = list; order.Add(group); }
            list.Add(name);
        }
        var result = new List<StepGroup>();
        foreach (var g in order)
            result.Add(new StepGroup(g, GroupLabel(g), true, false, byGroup[g]));
        // Every map group is ticked, arenas too (Kiln itself makes them only on request): the port plays
        // arena matches, and a map without its paks cannot be entered. mapDefault is Kiln's own choice.
        foreach (var g in mapGroups)
            result.Add(new StepGroup(g, GroupLabel(g), true, true, Array.Empty<string>()));
        return result;
    }

    public static List<StepGroup> FallbackGroups() => new()
    {
        new("tables", GroupLabel("tables"), true, false, Array.Empty<string>()),
        new("interface", GroupLabel("interface"), true, false, Array.Empty<string>()),
        new("audio", GroupLabel("audio"), true, false, Array.Empty<string>()),
        new("models", GroupLabel("models"), true, false, Array.Empty<string>()),
        new("world", GroupLabel("world"), true, false, new[] { "worldmap" }),
        new("continents", GroupLabel("continents"), true, true, Array.Empty<string>()),
        new("dungeons", GroupLabel("dungeons"), true, true, Array.Empty<string>()),
        new("battlegrounds", GroupLabel("battlegrounds"), true, true, Array.Empty<string>()),
        new("arenas", GroupLabel("arenas"), true, true, Array.Empty<string>()),
    };

    private static string GroupLabel(string g) => g switch
    {
        "tables" => "tables (texts, spells, flight paths)",
        "interface" => "interface (art, fonts, menu art)",
        "audio" => "audio (sounds, voices, music)",
        "models" => "models (characters, creatures, items)",
        "world" => "world map",
        "continents" => "maps: open world",
        "dungeons" => "maps: dungeons and raids",
        "battlegrounds" => "maps: battlegrounds",
        "arenas" => "maps: arenas",
        _ => g,
    };

    /// The --steps tokens for the ticked groups. A static group goes by its name, except one whose
    /// name Kiln also reads as "every map" ("world", "maps"): that one goes by its step names, so that
    /// unticked map groups stay out.
    public static List<string> StepTokens(IEnumerable<StepGroup> ticked)
    {
        var t = new List<string>();
        foreach (var g in ticked)
        {
            if (!g.IsMapGroup && (g.Id == "world" || g.Id == "maps") && g.Steps.Count > 0) t.AddRange(g.Steps);
            else t.Add(g.Id);
        }
        return t;
    }

    public sealed record TextResult(int ExitCode, string Output);

    private static async Task<TextResult> RunTextAsync(KilnInstall inst, IEnumerable<string> args, TimeSpan timeout)
    {
        var psi = NewPsi(inst, args);
        using var p = new Process { StartInfo = psi };
        var sb = new StringBuilder();
        p.OutputDataReceived += (_, e) => { if (e.Data is not null) lock (sb) sb.AppendLine(e.Data); };
        p.ErrorDataReceived += (_, e) => { if (e.Data is not null) lock (sb) sb.AppendLine(e.Data); };
        try
        {
            p.Start();
        }
        catch (System.ComponentModel.Win32Exception ex)
        {
            return new TextResult(-1, "cannot start " + inst.Python + ": " + ex.Message);
        }
        p.BeginOutputReadLine();
        p.BeginErrorReadLine();
        using var cts = new CancellationTokenSource(timeout);
        try { await p.WaitForExitAsync(cts.Token); }
        catch (OperationCanceledException) { try { p.Kill(true); } catch { } return new TextResult(-1, sb.ToString()); }
        return new TextResult(p.ExitCode, sb.ToString());
    }

    internal static ProcessStartInfo NewPsi(KilnInstall inst, IEnumerable<string> args)
    {
        var psi = new ProcessStartInfo(inst.Python)
        {
            UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardOutput = true, RedirectStandardError = true, RedirectStandardInput = false,
            StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8,
            WorkingDirectory = inst.Root,
        };
        if (inst.Python == "py") psi.ArgumentList.Add("-3");
        psi.ArgumentList.Add(inst.Script);
        foreach (var a in args) psi.ArgumentList.Add(a);
        psi.Environment["PYTHONIOENCODING"] = "utf-8";
        psi.Environment["PYTHONUNBUFFERED"] = "1";
        return psi;
    }
}

/// One run of kiln.py with --json. Each stdout line that parses as a JSON object is an event; any
/// other line (stdout or stderr) is plain text for the log.
public sealed class KilnProcess
{
    public event Action<string, JsonElement>? Event;    // kind, document
    public event Action<string>? Text;

    public async Task<int> RunAsync(KilnInstall inst, IReadOnlyList<string> args, CancellationToken ct,
                                    ProcessPriorityClass priority = ProcessPriorityClass.Normal)
    {
        using var p = new Process { StartInfo = KilnCli.NewPsi(inst, args), EnableRaisingEvents = true };
        p.OutputDataReceived += (_, e) => { if (e.Data is not null) Line(e.Data); };
        p.ErrorDataReceived += (_, e) => { if (e.Data is not null) Text?.Invoke(e.Data); };
        try
        {
            p.Start();
        }
        catch (System.ComponentModel.Win32Exception ex)
        {
            Text?.Invoke("cannot start " + inst.Python + ": " + ex.Message);
            return -1;
        }
        try { if (priority != ProcessPriorityClass.Normal) p.PriorityClass = priority; } catch { }
        p.BeginOutputReadLine();
        p.BeginErrorReadLine();
        // Cancel kills the whole tree: Python, the converter it is waiting on and that one's workers.
        using (ct.Register(() => { try { if (!p.HasExited) p.Kill(entireProcessTree: true); } catch { } }))
        {
            await p.WaitForExitAsync(CancellationToken.None);
        }
        p.WaitForExit();                                 // drains the redirected streams
        return p.ExitCode;
    }

    private void Line(string line)
    {
        var t = line.TrimStart();
        if (t.StartsWith('{'))
        {
            try
            {
                using var doc = JsonDocument.Parse(t);
                if (doc.RootElement.ValueKind == JsonValueKind.Object &&
                    doc.RootElement.TryGetProperty("event", out var ev) && ev.ValueKind == JsonValueKind.String)
                {
                    Event?.Invoke(ev.GetString()!, doc.RootElement.Clone());
                    return;
                }
            }
            catch (JsonException) { }
        }
        Text?.Invoke(line);
    }
}

public static class Json
{
    public static string? Str(this JsonElement e, string name) =>
        e.ValueKind == JsonValueKind.Object && e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;

    public static double? Num(this JsonElement e, string name) =>
        e.ValueKind == JsonValueKind.Object && e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetDouble() : null;

    public static bool? Bool(this JsonElement e, string name) =>
        e.ValueKind == JsonValueKind.Object && e.TryGetProperty(name, out var v) &&
        (v.ValueKind == JsonValueKind.True || v.ValueKind == JsonValueKind.False) ? v.GetBoolean() : null;

    public static IEnumerable<JsonElement> Arr(this JsonElement e, string name) =>
        e.ValueKind == JsonValueKind.Object && e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Array
            ? v.EnumerateArray() : Enumerable.Empty<JsonElement>();

    public static IEnumerable<string> Strs(this JsonElement e, string name) =>
        e.Arr(name).Where(x => x.ValueKind == JsonValueKind.String).Select(x => x.GetString()!);

    public static JsonElement? Obj(this JsonElement e, string name) =>
        e.ValueKind == JsonValueKind.Object && e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Object ? v : null;
}
