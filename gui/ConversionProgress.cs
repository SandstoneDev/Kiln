using System.Globalization;
using System.Text.Json;

namespace Kiln;

/// Measured work in the current converter phase; elapsed time never supplies its fraction.
public sealed record ConversionProgress(string Phase, long Completed, long Total, string Detail, long Failed = 0)
{
    public double? Fraction => Total > 0 ? Math.Clamp((double)Completed / Total, 0, 1) : null;
    public string Label => (Total > 0 ? $"{Phase} {Completed}/{Total}" : Phase) + (Failed > 0 ? $" ({Failed} failed)" : "");

    public static ConversionProgress? Parse(JsonElement value)
    {
        if (value.ValueKind != JsonValueKind.Object ||
            !value.TryGetProperty("phase", out var phase) || phase.ValueKind != JsonValueKind.String ||
            !Count(value, "completed", out long done) || !Count(value, "total", out long total) || done > total ||
            !Number(value, "elapsed_s", out _) ||
            !value.TryGetProperty("active", out var active) || active.ValueKind != JsonValueKind.Array) return null;
        long failed = 0;
        if (value.TryGetProperty("failed", out _) && (!Count(value, "failed", out failed) || failed > done)) return null;
        var workers = new List<string>();
        foreach (var item in active.EnumerateArray())
        {
            if (item.ValueKind != JsonValueKind.Object ||
                !Count(item, "model_id", out long id) || !Count(item, "work_done", out long work) ||
                !Count(item, "work_total", out long workTotal) || (workTotal > 0 && work > workTotal) ||
                !Number(item, "stage_elapsed_s", out double elapsed) ||
                !item.TryGetProperty("model", out var model) || model.ValueKind != JsonValueKind.String ||
                !item.TryGetProperty("stage", out var stage) || stage.ValueKind != JsonValueKind.String) return null;
            if (workers.Count < 3)
                workers.Add($"#{id} {model.GetString()}: {stage.GetString()}" +
                    (workTotal > 0 ? $" {work}/{workTotal}" : "") +
                    $" ({elapsed.ToString("0", CultureInfo.InvariantCulture)}s)");
        }
        return new ConversionProgress(phase.GetString() ?? "working", done, total, string.Join("; ", workers), failed);
    }

    private static bool Count(JsonElement value, string name, out long count)
    {
        count = 0;
        return value.TryGetProperty(name, out var part) && part.ValueKind == JsonValueKind.Number &&
               part.TryGetInt64(out count) && count >= 0;
    }

    private static bool Number(JsonElement value, string name, out double number)
    {
        number = 0;
        return value.TryGetProperty(name, out var part) && part.ValueKind == JsonValueKind.Number &&
               part.TryGetDouble(out number) && double.IsFinite(number) && number >= 0;
    }
}
