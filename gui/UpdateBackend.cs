using System.Diagnostics;
using System.Text;
using System.Text.Json;

namespace Kiln;

public enum UpdateAction { Check, DownloadKiln, DownloadEngine, InstallEngine, PrepareEngine }

/// Official release operations for the selected installation and the update dialog.
public static class UpdateBackend
{
    public static bool CanCancel(UpdateAction action) => action != UpdateAction.InstallEngine;

    /// Both the PC output and the actual card installation own runtime data.
    public static IReadOnlyList<string> PreparationTargets(string output, string? target)
    {
        var targets = new List<string> { Path.TrimEndingDirectorySeparator(Path.GetFullPath(output)) };
        if (!string.IsNullOrWhiteSpace(target))
        {
            string installed = Path.TrimEndingDirectorySeparator(Path.GetFullPath(target));
            if (Path.GetFileName(installed).Equals("data", StringComparison.OrdinalIgnoreCase))
                installed = Path.GetDirectoryName(installed)!;
            if (!targets.Contains(installed, StringComparer.OrdinalIgnoreCase)) targets.Add(installed);
        }
        return targets;
    }

    public static List<string> Args(UpdateAction action, string? target, string? destination)
    {
        var args = new List<string> { "-m", "kilnlib.updates" };
        if (action == UpdateAction.Check) args.Add("check");
        else
        {
            if (string.IsNullOrWhiteSpace(destination)) throw new ArgumentException("Choose a download folder on this PC.");
            args.Add(action == UpdateAction.InstallEngine ? "update-engine" :
                action == UpdateAction.PrepareEngine ? "ensure-engine" : "download");
            if (action is not (UpdateAction.InstallEngine or UpdateAction.PrepareEngine))
                args.AddRange(new[] { "--product", action == UpdateAction.DownloadKiln ? "kiln" : "engine" });
            args.AddRange(new[] { "--destination", destination });
        }
        if (!string.IsNullOrWhiteSpace(target) && action is UpdateAction.Check or UpdateAction.InstallEngine or UpdateAction.PrepareEngine)
            args.AddRange(new[] { "--target", target });
        else if (action is UpdateAction.InstallEngine or UpdateAction.PrepareEngine) throw new ArgumentException("Choose the game installation folder first.");
        return args;
    }

    public static async Task<JsonElement> RunAsync(KilnInstall inst, UpdateAction action,
        string? target, string? destination, CancellationToken cancellationToken)
    {
        var psi = new ProcessStartInfo(inst.Python)
        {
            UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = inst.Root,
            RedirectStandardOutput = true, RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8,
        };
        if (inst.Python == "py") psi.ArgumentList.Add("-3");
        foreach (var arg in Args(action, target, destination)) psi.ArgumentList.Add(arg);
        string? cancelFile = CanCancel(action) ? Path.Combine(Path.GetTempPath(), "kiln-update-cancel-" + Guid.NewGuid().ToString("N")) : null;
        if (cancelFile is not null) { psi.ArgumentList.Add("--cancel-file"); psi.ArgumentList.Add(cancelFile); }
        psi.Environment["PYTHONIOENCODING"] = "utf-8";
        using var process = new Process { StartInfo = psi };
        process.Start();
        var stdout = process.StandardOutput.ReadToEndAsync();
        var stderr = process.StandardError.ReadToEndAsync();
        // Cooperate with Python so cancellation also removes its partial
        // download. Killing it would skip its finally/rollback handlers.
        using var registration = CanCancel(action) ? cancellationToken.Register(() =>
        {
            try { File.WriteAllText(cancelFile!, "cancel"); }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException) { }
        }) : default;
        string output, errors;
        try
        {
            await process.WaitForExitAsync(CancellationToken.None);
            output = await stdout; errors = await stderr;
        }
        finally
        {
            registration.Dispose();
            if (cancelFile is not null)
                try { File.Delete(cancelFile); } catch (Exception e) when (e is IOException or UnauthorizedAccessException) { }
        }
        if (CanCancel(action)) cancellationToken.ThrowIfCancellationRequested();
        JsonDocument doc;
        try { doc = JsonDocument.Parse(output); }
        catch (JsonException) { throw new InvalidOperationException("The update service did not return a result. " + errors.Trim()); }
        using (doc)
        {
            var result = doc.RootElement;
            if (result.TryGetProperty("error", out var error))
                throw new InvalidOperationException(error.ValueKind == JsonValueKind.Object ? error.Str("message") ?? error.ToString() : error.ToString());
            if (process.ExitCode != 0) throw new InvalidOperationException("Update failed: " + errors.Trim());
            return result.Clone();
        }
    }
}
