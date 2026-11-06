using System.Text.Json;

namespace Kiln;

public sealed record AssetUpdateLocation(string Out, string? Target, bool OnCard);
public sealed record AssetUpdateRow(string Name, string Title, string State, string Reason, bool Selected);

public static class AssetUpdateBackend
{
    public static bool HasManifest(string? folder) => !string.IsNullOrWhiteSpace(folder) &&
        File.Exists(Path.Combine(folder, "data", "kiln.manifest.json"));

    public static AssetUpdateLocation Resolve(string pcOut, string? card, string work)
    {
        if (HasManifest(pcOut)) return new(Path.GetFullPath(pcOut), card, false);
        if (HasManifest(card))
        {
            if (string.IsNullOrWhiteSpace(work)) throw new ArgumentException("Choose a temporary work folder on the PC in Advanced settings.");
            string fullWork = Path.GetFullPath(work), fullCard = Path.GetFullPath(card!);
            if (string.Equals(Path.GetPathRoot(fullWork), Path.GetPathRoot(fullCard), StringComparison.OrdinalIgnoreCase) ||
                fullWork.Equals(fullCard, StringComparison.OrdinalIgnoreCase) ||
                fullWork.StartsWith(fullCard.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                throw new ArgumentException("The temporary work folder must be on the PC, outside the installed game folder.");
            return new(fullCard, null, true);
        }
        throw new ArgumentException("No Kiln manifest was found in the PC data folder or the selected installation. Choose the earlier conversion or restore its manifest first.");
    }

    public static List<string> PlanArgs(RunOptions options, KilnInstall inst, KilnCaps caps)
    {
        var args = KilnCli.Args(options, inst, caps);
        args.Remove("--update");
        args.Add("--plan-update");
        // A preview, and resource updates generally, must not replace a saved
        // server or select an old explicitly cached EBOOT.
        foreach (string flag in new[] { "--server", "--eboot" })
        {
            int i = args.IndexOf(flag);
            if (i >= 0) args.RemoveRange(i, 2);
        }
        return args;
    }

    public static List<AssetUpdateRow> Rows(JsonElement plan) => plan.Arr("steps").Select(step =>
        new AssetUpdateRow(step.Str("name") ?? "", step.Str("title") ?? "", step.Str("state") ?? "run",
            step.Str("reason") ?? "Already up to date", step.Bool("selected") ?? false)).Where(row => row.Name.Length > 0).ToList();
}
