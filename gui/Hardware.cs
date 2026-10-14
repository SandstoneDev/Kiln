// What the PC has: physical CPU cores (for the default job count), which local drive is fastest
// (for the default work folder) and which removable drives are PSP memory sticks.
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace Kiln;

public static class Hardware
{
    // ---- CPU -----------------------------------------------------------------------------------
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetLogicalProcessorInformationEx(int relationship, IntPtr buffer, ref uint length);

    /// Physical cores (records of RelationProcessorCore); the logical count when the API fails.
    public static int PhysicalCores()
    {
        try
        {
            uint len = 0;
            GetLogicalProcessorInformationEx(0, IntPtr.Zero, ref len);
            if (len == 0) return Environment.ProcessorCount;
            IntPtr buf = Marshal.AllocHGlobal((int)len);
            try
            {
                if (!GetLogicalProcessorInformationEx(0, buf, ref len)) return Environment.ProcessorCount;
                int n = 0, off = 0;
                while (off < len)
                {
                    int size = Marshal.ReadInt32(buf, off + 4);     // { int Relationship; uint Size; ... }
                    if (size <= 0) break;
                    n++;
                    off += size;
                }
                return n > 0 ? n : Environment.ProcessorCount;
            }
            finally { Marshal.FreeHGlobal(buf); }
        }
        catch (Exception) { return Environment.ProcessorCount; }
    }

    /// The default job count: one per physical core, and at least one hardware thread left free.
    public static int DefaultJobs()
    {
        int logical = Environment.ProcessorCount;
        return Math.Max(1, Math.Min(PhysicalCores(), logical - 1));
    }

    // ---- drives ----------------------------------------------------------------------------------
    public enum DriveKind { Unknown = 0, Hdd = 1, Ssd = 2, Nvme = 3 }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFile(string name, uint access, uint share, IntPtr sec, uint disposition, uint flags, IntPtr template);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool DeviceIoControl(SafeFileHandle h, uint code, byte[] inBuf, int inSize, byte[] outBuf, int outSize, out int returned, IntPtr overlapped);

    // CTL_CODE(IOCTL_STORAGE_BASE = 0x2D, function 0x500, METHOD_BUFFERED, FILE_ANY_ACCESS)
    private const uint IoctlStorageQueryProperty = (0x2Du << 16) | (0x500u << 2);
    private const int StorageDeviceProperty = 0, StorageDeviceSeekPenaltyProperty = 7;
    private const int BusTypeNvme = 17;

    private static byte[]? Query(SafeFileHandle h, int property, int outSize)
    {
        var q = new byte[12];
        BitConverter.GetBytes(property).CopyTo(q, 0);           // PropertyId; QueryType 0 = standard
        var o = new byte[outSize];
        return DeviceIoControl(h, IoctlStorageQueryProperty, q, q.Length, o, o.Length, out int got, IntPtr.Zero) && got > 0 ? o : null;
    }

    /// NVMe / SSD / HDD of a drive letter (no admin rights needed: the volume is opened with no access).
    public static DriveKind KindOf(string root)
    {
        try
        {
            string letter = root.TrimEnd('\\', '/');
            using var h = CreateFile(@"\\.\" + letter, 0, 3, IntPtr.Zero, 3, 0, IntPtr.Zero);
            if (h.IsInvalid) return DriveKind.Unknown;
            var dev = Query(h, StorageDeviceProperty, 1024);
            if (dev is not null && BitConverter.ToInt32(dev, 28) == BusTypeNvme) return DriveKind.Nvme;
            var seek = Query(h, StorageDeviceSeekPenaltyProperty, 12);
            if (seek is not null) return seek[8] == 0 ? DriveKind.Ssd : DriveKind.Hdd;
        }
        catch (Exception) { }
        return DriveKind.Unknown;
    }

    public static string KindText(DriveKind k) => k switch
    {
        DriveKind.Nvme => "NVMe SSD",
        DriveKind.Ssd => "SSD",
        DriveKind.Hdd => "hard disk",
        _ => "disk",
    };

    public sealed record DriveChoice(string Root, DriveKind Kind, long Free, long Size);

    /// Local fixed drives with room for `need` bytes and a margin (10 GB or a tenth of the drive,
    /// whichever is more, stays free), fastest first, then most free space. The best one hosts the
    /// work folder (and the PC copy of the data when the data goes to a memory stick).
    public static List<DriveChoice> WorkDrives(long need)
    {
        var list = new List<DriveChoice>();
        foreach (var d in DriveInfo.GetDrives())
        {
            try
            {
                if (d.DriveType != DriveType.Fixed || !d.IsReady) continue;
                list.Add(new DriveChoice(d.RootDirectory.FullName, KindOf(d.RootDirectory.FullName), d.AvailableFreeSpace, d.TotalSize));
            }
            catch (Exception) { }
        }
        static long Margin(DriveChoice c) => Math.Max(10L << 30, c.Size / 10);
        return list.Where(c => c.Free >= need + Margin(c))
                   .OrderByDescending(c => (int)c.Kind).ThenByDescending(c => c.Free)
                   .Concat(list.Where(c => c.Free < need + Margin(c)).OrderByDescending(c => c.Free))
                   .ToList();
    }

    // ---- memory sticks ----------------------------------------------------------------------------
    public sealed record Stick(string Root, string Label, string Format, long Free, long Size)
    {
        public override string ToString() =>
            $"{Root.TrimEnd('\\')}  {(Label.Length > 0 ? Label + ", " : "")}{Format}, {Disk.Gb(Free)} free of {Disk.Gb(Size)}";
    }

    /// Removable drives that hold a PSP folder (a memory stick in a card reader, or a PSP in USB mode).
    public static List<Stick> Sticks()
    {
        var list = new List<Stick>();
        foreach (var d in DriveInfo.GetDrives())
        {
            try
            {
                if (d.DriveType != DriveType.Removable || !d.IsReady) continue;
                if (!Directory.Exists(Path.Combine(d.RootDirectory.FullName, "PSP"))) continue;
                list.Add(new Stick(d.RootDirectory.FullName, d.VolumeLabel ?? "", d.DriveFormat, d.AvailableFreeSpace, d.TotalSize));
            }
            catch (Exception) { }
        }
        return list;
    }
}
