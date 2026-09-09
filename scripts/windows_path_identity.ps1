if (-not ("SubtitleEditBay.WindowsPathIdentity" -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

namespace SubtitleEditBay {
    public static class WindowsPathIdentity {
        private const uint FILE_SHARE_READ = 0x00000001;
        private const uint FILE_SHARE_WRITE = 0x00000002;
        private const uint FILE_SHARE_DELETE = 0x00000004;
        private const uint OPEN_EXISTING = 3;
        private const uint FILE_FLAG_BACKUP_SEMANTICS = 0x02000000;

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern SafeFileHandle CreateFile(
            string name,
            uint access,
            uint share,
            IntPtr security,
            uint creation,
            uint flags,
            IntPtr template);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern uint GetFinalPathNameByHandle(
            SafeFileHandle handle,
            StringBuilder path,
            uint length,
            uint flags);

        public static string ResolveDirectory(string path) {
            using (SafeFileHandle handle = CreateFile(
                path,
                0,
                FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                IntPtr.Zero,
                OPEN_EXISTING,
                FILE_FLAG_BACKUP_SEMANTICS,
                IntPtr.Zero)) {
                if (handle.IsInvalid) {
                    throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
                }
                StringBuilder buffer = new StringBuilder(32768);
                uint length = GetFinalPathNameByHandle(handle, buffer, (uint)buffer.Capacity, 0);
                if (length == 0 || length >= buffer.Capacity) {
                    throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
                }
                string resolved = buffer.ToString();
                if (resolved.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase)) {
                    return @"\\" + resolved.Substring(8);
                }
                if (resolved.StartsWith(@"\\?\", StringComparison.OrdinalIgnoreCase)) {
                    return resolved.Substring(4);
                }
                return resolved;
            }
        }
    }
}
'@
}

function Resolve-FinalDirectoryPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $fullPath = [IO.Path]::GetFullPath($Path)
    return [SubtitleEditBay.WindowsPathIdentity]::ResolveDirectory($fullPath)
}
