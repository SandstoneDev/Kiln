// Kiln.exe: no arguments = the window. One command-line verb, for testing a bundle without clicking:
//
//   Kiln.exe --dry-run --game <client folder> --out <output> [--add-locale DIR ...] [--default-locale xxYY]
//            [--steps LIST] [--jobs N] [--work DIR]
//
// runs the same dry run the window runs (kiln.py --dry-run --json through the same back end) and
// prints what the window would show. Exit code: kiln.py's.
using System.Runtime.InteropServices;
using System.Text.Json;

namespace Kiln;

public static class Program
{
    [DllImport("kernel32.dll")]
    private static extern bool AttachConsole(int pid);

    [STAThread]
    public static int Main(string[] args)
    {
        if (args.Length == 0)
        {
            ApplicationConfiguration.Initialize();
            Application.Run(new MainForm());
            return 0;
        }
        if (!Console.IsOutputRedirected) AttachConsole(-1);    // a WinExe has no console of its own
        try
        {
            if (args[0] == "--dry-run") return DryRun(args.Skip(1).ToArray()).GetAwaiter().GetResult();
            Console.WriteLine("usage: Kiln.exe  (the window)\n       Kiln.exe --dry-run --game DIR --out DIR [--add-locale DIR ...] [--default-locale xxYY] [--steps LIST] [--jobs N] [--work DIR]");
            return 64;
        }
        catch (Exception ex)
        {
            Console.WriteLine("error: " + ex.Message);
            return 1;
        }
    }

    private static async Task<int> DryRun(string[] a)
    {
        var inst = KilnInstall.Find();
        if (inst is null) { Console.WriteLine("kiln.py not found next to Kiln.exe"); return 2; }
        var o = new RunOptions { Mode = KilnMode.DryRun };
        for (int i = 0; i < a.Length; i++)
        {
            string Next() => i + 1 < a.Length ? a[++i] : throw new ArgumentException("missing value after " + a[i]);
            switch (a[i])
            {
                case "--game": o.Game = Next(); break;
                case "--out": o.Out = Next(); break;
                case "--work": o.Work = Next(); break;
                case "--add-locale": o.AddLocales.Add(Next()); break;
                case "--default-locale": o.DefaultLocale = Next(); break;
                case "--steps": o.Steps = Next().Split(',', StringSplitOptions.RemoveEmptyEntries).ToList(); break;
                case "--jobs": o.Jobs = int.Parse(Next()); break;
                default: throw new ArgumentException("unknown option " + a[i]);
            }
        }
        Console.WriteLine($"Kiln {inst.Version}: kiln.py in {inst.Root}, Python {inst.Python} ({(inst.Bundled ? "bundled" : "not bundled")})");
        var caps = await KilnCli.ReadCapsAsync(inst);
        var groups = await KilnCli.ReadGroupsAsync(inst);
        Console.WriteLine($"options: {caps.Options.Count} ({(caps.Has("--add-locale") ? "with" : "without")} --add-locale)");
        Console.WriteLine("step groups: " + string.Join(", ", groups.Select(g => g.Id + (g.Default ? "" : " (off)"))));
        if (o.Steps.Count == 0) o.Steps = KilnCli.StepTokens(groups.Where(g => g.Default));
        var args = KilnCli.Args(o, inst, caps);
        Console.WriteLine("$ " + KilnCli.Show(inst, args));

        var p = new KilnProcess();
        p.Text += t => Console.WriteLine("  | " + t);
        p.Event += (kind, e) =>
        {
            switch (kind)
            {
                case "client":
                    Console.WriteLine($"client: {e.Str("label")}; primary {e.Str("primary")}; locales {string.Join(", ", e.Strs("locales"))}" +
                                      (e.Str("default") is { } d ? $"; starts in {d}" : ""));
                    break;
                case "error":
                    Console.WriteLine("error: " + e.Str("message"));
                    break;
                case "plan":
                    var steps = e.Arr("steps").ToList();
                    var byState = steps.GroupBy(s => s.Str("state") ?? "?").Select(g => $"{g.Count()} {g.Key}");
                    Console.WriteLine($"plan: {steps.Count} steps ({string.Join(", ", byState)})");
                    foreach (var s in steps)
                        Console.WriteLine($"   {s.Str("name"),-28} {s.Str("group"),-12} {s.Str("locale"),-6} {s.Str("state")}");
                    if (e.Str("maps_note") is { } mn) Console.WriteLine("note: " + mn);
                    break;
                case "log":
                    Console.WriteLine("  " + e.Str("text"));
                    break;
            }
        };
        int rc = await p.RunAsync(inst, args, CancellationToken.None);
        Console.WriteLine($"kiln.py exit code {rc}");
        return rc;
    }
}
