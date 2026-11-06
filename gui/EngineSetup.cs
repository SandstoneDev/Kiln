using System.Text.Json;

namespace Kiln;

public sealed partial class MainForm
{
    private readonly Label _installationInfo = new() { AutoSize = true, MaximumSize = new Size(900, 0) };
    private CancellationTokenSource? _installationCheckCts;
    private string _installationCheckKey = "";
    private bool _preparingEngine;

    private async Task<bool> PrepareEngineAsync(RunOptions options)
    {
        _preparingEngine = true;
        _lastDone = null;
        _phaseText = "preparing the engine in the selected installation folder";
        SetStatus("Preparing engine 0.2.0 or newer: compatible builds are kept; an older or missing engine is updated from the official release...", null);
        try
        {
            string cache = Path.Combine(options.Work ?? WorkDir(), "releases");
            foreach (string installation in UpdateBackend.PreparationTargets(options.Out, options.Target))
            {
                Log("== prepare engine in " + installation);
                var result = await UpdateBackend.RunAsync(_inst!, UpdateAction.PrepareEngine,
                    installation, cache, _runCts!.Token);
                if (_cancelled) return false;
                Log($"Engine {result.Str("version")}: {result.Str("status")} in {installation}");
            }
            options.Eboot = null; // the prepared installation owns its engine, including local hardware builds
            _installationCheckKey = "";
            return true;
        }
        catch (OperationCanceledException) { Log("Engine preparation cancelled; conversion was not started."); }
        catch (Exception e) when (e is IOException or InvalidOperationException or ArgumentException or System.ComponentModel.Win32Exception or UnauthorizedAccessException)
        {
            Log("Engine preparation failed: " + e.Message);
            SetSummary("Engine preparation failed; conversion was not started.", Bad,
                new List<string> { e.Message, "Your existing resources were kept. Retry Start when the problem is resolved." }, null);
            SetStatus(e.Message, Bad);
            _tabs.SelectedIndex = 2;
        }
        finally { _preparingEngine = false; _phaseText = ""; }
        return false;
    }

    private async Task RefreshInstallationStatusAsync()
    {
        if (_inst is null || !_ready || _busy || PcOut().Length == 0) return;
        string target = StickGameDir() ?? PcOut();
        string stamp = "";
        try
        {
            stamp = File.GetLastWriteTimeUtc(Path.Combine(target, "EBOOT.PBP")).Ticks + ":" +
                File.GetLastWriteTimeUtc(Path.Combine(target, "data", "kiln.manifest.json")).Ticks;
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or ArgumentException) { return; }
        string key = target + ":" + stamp + ":" + _game.Text.Trim() + ":" + WorkDir() + ":" + _clientOk;
        if (key == _installationCheckKey) return;
        _installationCheckKey = key;
        _installationCheckCts?.Cancel();
        using var cts = new CancellationTokenSource(); _installationCheckCts = cts;
        _installationInfo.Text = "Checking installed programs and resources...";
        _installationInfo.ForeColor = HelpColor;
        string resourceInfo = " Resources: choose your client folder to check or prepare them.";
        bool resourcesChanged = false;
        try
        {
            if (_clientOk && AssetUpdateBackend.HasManifest(target) && _caps.Has("--plan-update"))
            {
                var options = Options(KilnMode.Update);
                options.Out = target; options.Target = null;
                options.Steps.Clear(); // installed packs and regions, not the default full-conversion selection
                JsonElement? plan = null;
                var process = new KilnProcess();
                process.Event += (kind, doc) => { if (kind == "plan") plan = doc.Clone(); };
                int rc = await process.RunAsync(_inst, AssetUpdateBackend.PlanArgs(options, _inst, _caps), cts.Token);
                if (cts.IsCancellationRequested || _busy || key != _installationCheckKey) return;
                if (rc == 0 && plan is { } p)
                {
                    int changed = AssetUpdateBackend.Rows(p).Count(row => row.Selected);
                    resourcesChanged = changed > 0;
                    resourceInfo = changed == 0 ? " Resources: up to date." : $" Resources: {changed} pack(s) need updating (Update assets).";
                }
                else resourceInfo = " Resources: could not check; see Update assets.";
            }
            else if (_clientOk) resourceInfo = " Resources: Start prepares them from your client.";
            var releases = await UpdateBackend.RunAsync(_inst, UpdateAction.Check, target, null, cts.Token);
            if (cts.IsCancellationRequested || _busy || key != _installationCheckKey || target != (StickGameDir() ?? PcOut())) return;
            var engine = releases.Obj("engine");
            var kiln = releases.Obj("kiln");
            string info = engine?.Str("current") is not { } version
                ? "Engine: Start will download and install the latest official release."
                : engine?.Bool("update_available") == true
                    ? $"Engine {version}: update {engine?.Str("latest")} available (Check for updates)."
                    : $"Engine {version}: installed; no newer release.";
            if (kiln?.Bool("update_available") == true)
                info += $" Kiln {kiln?.Str("latest")} available (Check for updates).";
            else info += " Kiln: no newer release.";
            info += resourceInfo;
            _installationInfo.Text = info;
            _installationInfo.ForeColor = resourcesChanged || engine?.Bool("update_available") == true || kiln?.Bool("update_available") == true ? Warn : Good;
        }
        catch (OperationCanceledException) { }
        catch (Exception e) when (e is IOException or InvalidOperationException or ArgumentException or System.ComponentModel.Win32Exception or UnauthorizedAccessException)
        {
            if (key != _installationCheckKey || _busy) return;
            _installationInfo.Text = "Update check unavailable: " + e.Message +
                " An installed engine can be used offline; Check for updates retries the release check." + resourceInfo;
            _installationInfo.ForeColor = Warn;
        }
        finally { if (ReferenceEquals(_installationCheckCts, cts)) _installationCheckCts = null; }
    }
}
