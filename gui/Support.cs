// Small helpers: what a client folder holds, where an output goes, how much room there is, what the
// form remembers between runs and how long steps took on this PC.
using System.Text.Json;
using System.Text.RegularExpressions;

namespace Kiln;

public static class ClientScan
{
    /// The locales of a client folder: Data/<xxYY>/ folders that hold locale-<xxYY>.MPQ (the same rule
    /// kiln.py uses). Shown at once; kiln.py's dry run is what decides.
    public static List<string> Locales(string gameDir)
    {
        var found = new List<string>();
        try
        {
            var data = Directory.EnumerateDirectories(gameDir)
                                .FirstOrDefault(d => string.Equals(Path.GetFileName(d), "data", StringComparison.OrdinalIgnoreCase));
            if (data is null) return found;
            foreach (var sub in Directory.EnumerateDirectories(data).OrderBy(x => x, StringComparer.Ordinal))
            {
                string name = Path.GetFileName(sub);
                if (!Regex.IsMatch(name, "^[a-z]{2}[A-Z]{2}$")) continue;
                string want = ("locale-" + name + ".mpq").ToLowerInvariant();
                if (Directory.EnumerateFiles(sub).Any(f => Path.GetFileName(f).ToLowerInvariant() == want))
                    found.Add(name);
            }
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
        return found;
    }

    /// The locale Kiln writes to data/ when nothing else is said: the only one, else English, else the
    /// first by name (kilnlib/client.py primary_locale).
    public static string Primary(List<string> locs)
    {
        if (locs.Count == 1) return locs[0];
        foreach (var p in new[] { "enUS", "enGB" }) if (locs.Contains(p)) return p;
        return locs.OrderBy(x => x, StringComparer.Ordinal).FirstOrDefault() ?? "";
    }

    /// data/locales.txt of a conversion: primary, extra languages, start language. Null when absent.
    public static (string primary, List<string> extra, string start)? ReadLocales(string dataDir)
    {
        try
        {
            string p = Path.Combine(dataDir, "locales.txt");
            if (!File.Exists(p)) return null;
            string primary = "", start = "";
            var extra = new List<string>();
            foreach (var raw in File.ReadAllLines(p))
            {
                var t = raw.Trim().Split(' ', 2, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
                if (t.Length < 2 || t[0].StartsWith('#')) continue;
                if (t[0] == "primary") primary = t[1];
                else if (t[0] == "extra") extra.Add(t[1]);
                else if (t[0] == "default") start = t[1];
            }
            return primary.Length == 0 ? null : (primary, extra, start.Length > 0 ? start : primary);
        }
        catch (IOException) { return null; }
        catch (UnauthorizedAccessException) { return null; }
    }

    public static bool HasData(string gameDir)
    {
        try
        {
            return Directory.Exists(gameDir) && Directory.EnumerateDirectories(gameDir)
                .Any(d => string.Equals(Path.GetFileName(d), "data", StringComparison.OrdinalIgnoreCase));
        }
        catch (IOException) { return false; }
        catch (UnauthorizedAccessException) { return false; }
    }

    public static bool Inside(string path, string root)
    {
        if (string.IsNullOrWhiteSpace(path) || string.IsNullOrWhiteSpace(root)) return false;
        try
        {
            string p = Path.GetFullPath(path).TrimEnd('\\', '/'), r = Path.GetFullPath(root).TrimEnd('\\', '/');
            return p.Equals(r, StringComparison.OrdinalIgnoreCase) ||
                   p.StartsWith(r + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);
        }
        catch (Exception) { return false; }
    }
}

public static class Disk
{
    // What a full run needs (README: roughly 10 GB for the work folder and 10 GB for the output).
    public const long DataBytes = 10L << 30;
    public const long WorkBytes = 10L << 30;

    public static DriveInfo? DriveOf(string path)
    {
        try
        {
            string? root = Path.GetPathRoot(Path.GetFullPath(path));
            return string.IsNullOrEmpty(root) ? null : new DriveInfo(root);
        }
        catch (Exception) { return null; }
    }

    public static string Gb(long bytes) => (bytes / (double)(1L << 30)).ToString("0.0") + " GB";

    /// A PSP memory stick: a removable drive, or any drive root with a PSP folder.
    public static bool LooksLikeMemoryStick(string root)
    {
        try
        {
            var d = DriveOf(root);
            if (d is null || !d.IsReady) return false;
            return d.DriveType == DriveType.Removable || Directory.Exists(Path.Combine(d.RootDirectory.FullName, "PSP"));
        }
        catch (Exception) { return false; }
    }
}

/// What the form remembers (%LOCALAPPDATA%\Kiln\settings.json). Never anything from the game.
public sealed class Settings
{
    public string Game { get; set; } = "";
    public List<string> Extra { get; set; } = new();
    public string DefaultLocale { get; set; } = "";
    public string Dest { get; set; } = "";             // "stick" or "folder"
    public string Stick { get; set; } = "";            // the memory stick's drive root last used
    public string Out { get; set; } = "";              // the folder, when the data goes to a folder
    public string PcOut { get; set; } = "";            // the PC data folder, when the data goes to a stick
    public string Work { get; set; } = "";
    public string App { get; set; } = "WOWEEPSP";
    public string Eboot { get; set; } = "";
    public string Server { get; set; } = "";
    public int Jobs { get; set; }
    public List<string> Groups { get; set; } = new();
    public bool LowPriority { get; set; }
    public bool Advanced { get; set; }                 // the advanced settings were open

    public static string Dir => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Kiln");
    private static string FilePath => Path.Combine(Dir, "settings.json");

    public static Settings Load()
    {
        try
        {
            if (File.Exists(FilePath))
                return JsonSerializer.Deserialize<Settings>(File.ReadAllText(FilePath)) ?? new Settings();
        }
        catch (Exception) { }
        return new Settings();
    }

    public void Save()
    {
        try
        {
            Directory.CreateDirectory(Dir);
            File.WriteAllText(FilePath, JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true }));
        }
        catch (Exception) { }
    }
}

/// How long each step took on this PC (moving average of the last runs), so a first run into a new
/// output folder still gets an estimate. Kiln's manifest (last_seconds in the plan) wins when present.
public sealed class EtaStore
{
    private const int Window = 3;
    private readonly string _path = Path.Combine(Settings.Dir, "durations.json");
    private Dictionary<string, List<double>> _s = new();

    public EtaStore()
    {
        try
        {
            if (File.Exists(_path))
                _s = JsonSerializer.Deserialize<Dictionary<string, List<double>>>(File.ReadAllText(_path)) ?? new();
        }
        catch (Exception) { _s = new(); }
    }

    public double? Get(string key) => _s.TryGetValue(key, out var xs) && xs.Count > 0 ? xs.Average() : null;

    public void Record(string key, double seconds)
    {
        if (seconds <= 0) return;
        if (!_s.TryGetValue(key, out var xs)) { xs = new(); _s[key] = xs; }
        xs.Add(seconds);
        while (xs.Count > Window) xs.RemoveAt(0);
    }

    public void Save()
    {
        try
        {
            Directory.CreateDirectory(Settings.Dir);
            File.WriteAllText(_path, JsonSerializer.Serialize(_s));
        }
        catch (Exception) { }
    }

    /// A first guess for a step nobody has timed yet (seconds of one job).
    public static double Guess(string name, string group)
    {
        if (name.StartsWith("extract", StringComparison.Ordinal)) return 600;
        if (name == "png") return 120;
        if (group.StartsWith("maps", StringComparison.Ordinal))
            return name.StartsWith("doodads_", StringComparison.Ordinal) ? 600 : name.StartsWith("terrain_", StringComparison.Ordinal) ? 300 : 120;
        return group switch
        {
            "tables" => 30,
            "interface" => 90,
            "audio" => 300,
            "models" => 600,
            "world" => 300,
            _ => 60,
        };
    }
}
