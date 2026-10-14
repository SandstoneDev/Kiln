// The memory-stick copy of data\, done by the window while kiln.py has no --target: copy only the
// files that differ from the PC copy, and check the stick copy file by file (missing, wrong size,
// wrong SHA-256). The PC copy is the reference; kiln.py's manifest vouches for it.
using System.Security.Cryptography;
using System.Text.Json;

namespace Kiln;

public static class CardSync
{
    public sealed record Problem(string Path, string What);

    public sealed class CopyResult
    {
        public int Copied, Same;
        public long Bytes;
        public List<string> Errors = new();
        public List<string> Extra = new();            // on the stick, not in the PC copy (left alone)
    }

    private static IEnumerable<string> Files(string root) =>
        Directory.Exists(root)
            ? Directory.EnumerateFiles(root, "*", SearchOption.AllDirectories)
                       .Where(f => !f.EndsWith(".tmp", StringComparison.OrdinalIgnoreCase))
            : Enumerable.Empty<string>();

    /// Copy src (the PC data folder) to dst (the stick's data folder): a file is copied when it is
    /// missing, has another size, or is older on the stick (FAT32 keeps times to 2 s). `only`, when
    /// given, limits the copy to these relative paths (a repair).
    public static CopyResult Copy(string src, string dst, Action<string> log, Action<double> progress, CancellationToken ct,
                                  ISet<string>? only = null)
    {
        var r = new CopyResult();
        var files = Files(src).ToList();
        long total = Math.Max(1, files.Sum(f => new FileInfo(f).Length));
        long done = 0;
        foreach (var f in files)
        {
            ct.ThrowIfCancellationRequested();
            string rel = Path.GetRelativePath(src, f);
            var si = new FileInfo(f);
            if (only is not null && !only.Contains(rel.Replace('\\', '/'))) { done += si.Length; continue; }
            string to = Path.Combine(dst, rel);
            var di = new FileInfo(to);
            bool need = only is not null || !di.Exists || di.Length != si.Length ||
                        di.LastWriteTimeUtc < si.LastWriteTimeUtc.AddSeconds(-2);
            if (!need) { r.Same++; done += si.Length; progress(done / (double)total); continue; }
            if (si.Length >= 4L << 30) { r.Errors.Add($"{rel}: 4 GB or more does not fit a FAT32 memory stick"); continue; }
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(to)!);
                string tmp = to + ".kilntmp";
                CopyStream(f, tmp, ct, n => progress((done + n) / (double)total));
                File.Move(tmp, to, overwrite: true);
                File.SetLastWriteTimeUtc(to, si.LastWriteTimeUtc);
                r.Copied++;
                r.Bytes += si.Length;
                log($"copied {rel} ({si.Length / 1048576.0:0.0} MB)");
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException)
            {
                r.Errors.Add($"{rel}: {e.Message}");
            }
            done += si.Length;
            progress(done / (double)total);
        }
        if (Directory.Exists(dst))
        {
            var have = new HashSet<string>(files.Select(f => Path.GetRelativePath(src, f)), StringComparer.OrdinalIgnoreCase);
            r.Extra = Files(dst).Select(f => Path.GetRelativePath(dst, f)).Where(x => !have.Contains(x)).ToList();
        }
        return r;
    }

    private static void CopyStream(string from, string to, CancellationToken ct, Action<long> moved)
    {
        using var i = new FileStream(from, FileMode.Open, FileAccess.Read, FileShare.Read, 1 << 20);
        using var o = new FileStream(to, FileMode.Create, FileAccess.Write, FileShare.None, 1 << 20);
        var buf = new byte[4 << 20];
        long n = 0;
        int k;
        while ((k = i.Read(buf, 0, buf.Length)) > 0)
        {
            ct.ThrowIfCancellationRequested();
            o.Write(buf, 0, k);
            n += k;
            moved(n);
        }
    }

    /// Check the stick copy against the PC copy: every file of the PC data folder must be on the
    /// stick with the same size and SHA-256 (the PC side's hash comes from Kiln's manifest when it
    /// lists the file).
    public static List<Problem> Check(string src, string dst, Action<double> progress, CancellationToken ct)
    {
        var bad = new List<Problem>();
        var known = ManifestHashes(src);
        var files = Files(src).ToList();
        long total = Math.Max(1, files.Sum(f => new FileInfo(f).Length)), done = 0;
        foreach (var f in files)
        {
            ct.ThrowIfCancellationRequested();
            string rel = Path.GetRelativePath(src, f).Replace('\\', '/');
            var si = new FileInfo(f);
            var di = new FileInfo(Path.Combine(dst, rel));
            if (!di.Exists) bad.Add(new Problem(rel, "missing on the memory stick"));
            else if (di.Length != si.Length) bad.Add(new Problem(rel, $"size {di.Length} on the stick, {si.Length} on the PC"));
            else
            {
                string want = known.TryGetValue(rel, out var h) ? h : Sha256(f, ct);
                if (!string.Equals(Sha256(di.FullName, ct), want, StringComparison.OrdinalIgnoreCase))
                    bad.Add(new Problem(rel, "content differs (SHA-256)"));
            }
            done += si.Length;
            progress(done / (double)total);
        }
        return bad;
    }

    private static Dictionary<string, string> ManifestHashes(string dataDir)
    {
        var map = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        try
        {
            using var doc = JsonDocument.Parse(File.ReadAllText(Path.Combine(dataDir, "kiln.manifest.json")));
            if (doc.RootElement.Obj("steps") is { } steps)
                foreach (var st in steps.EnumerateObject())
                    foreach (var o in st.Value.Arr("outputs"))
                        if (o.Str("path") is { } p && o.Str("sha256") is { } h && p.StartsWith("data/", StringComparison.Ordinal))
                            map[p[5..]] = h;
        }
        catch (Exception) { }
        return map;
    }

    private static string Sha256(string path, CancellationToken ct)
    {
        using var s = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read, 1 << 20);
        using var sha = SHA256.Create();
        var buf = new byte[4 << 20];
        int k;
        while ((k = s.Read(buf, 0, buf.Length)) > 0)
        {
            ct.ThrowIfCancellationRequested();
            sha.TransformBlock(buf, 0, k, null, 0);
        }
        sha.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
        return Convert.ToHexString(sha.Hash!).ToLowerInvariant();
    }

    public static (int files, long bytes) Measure(string dir)
    {
        int n = 0;
        long b = 0;
        foreach (var f in Files(dir)) { n++; b += new FileInfo(f).Length; }
        return (n, b);
    }
}
