using System;
using System.Runtime.InteropServices;

public static class PiabShortcutAppId {
    const uint GPS_READWRITE = 2;
    const uint GPS_DEFAULT = 0;
    const ushort VT_LPWSTR = 31;

    static readonly Guid IID_IPropertyStore = new Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99");
    static PropertyKey AppUserModelIdKey() {
        return new PropertyKey {
            fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"),
            pid = 5
        };
    }

    public static void Set(string shortcutPath, string appId) {
        var iid = IID_IPropertyStore;
        IntPtr unk;
        int hr = SHGetPropertyStoreFromParsingNamePtr(shortcutPath, IntPtr.Zero, GPS_READWRITE, ref iid, out unk);
        if (hr < 0) {
            Marshal.ThrowExceptionForHR(hr);
        }
        if (unk == IntPtr.Zero) {
            throw new InvalidOperationException("No property store for " + shortcutPath);
        }
        var store = (IPropertyStore)Marshal.GetObjectForIUnknown(unk);
        Marshal.Release(unk);
        var pv = new PropVariant();
        pv.vt = VT_LPWSTR;
        pv.pointerValue = Marshal.StringToCoTaskMemUni(appId);
        try {
            var key = AppUserModelIdKey();
            hr = store.SetValue(ref key, ref pv);
            if (hr < 0) {
                Marshal.ThrowExceptionForHR(hr);
            }
            hr = store.Commit();
            if (hr < 0) {
                Marshal.ThrowExceptionForHR(hr);
            }
        } finally {
            Marshal.FreeCoTaskMem(pv.pointerValue);
            Marshal.ReleaseComObject(store);
        }
    }

    public static string Get(string shortcutPath) {
        var iid = IID_IPropertyStore;
        IntPtr unk;
        int hr = SHGetPropertyStoreFromParsingNamePtr(shortcutPath, IntPtr.Zero, GPS_READWRITE, ref iid, out unk);
        if (hr < 0) {
            Marshal.ThrowExceptionForHR(hr);
        }
        if (unk == IntPtr.Zero) {
            return "";
        }
        var store = (IPropertyStore)Marshal.GetObjectForIUnknown(unk);
        Marshal.Release(unk);
        try {
            var pv = new PropVariant();
            var key = AppUserModelIdKey();
            hr = store.GetValue(ref key, out pv);
            if (hr < 0) {
                Marshal.ThrowExceptionForHR(hr);
            }
            try {
                if (pv.vt != VT_LPWSTR || pv.pointerValue == IntPtr.Zero) {
                    return "";
                }
                return Marshal.PtrToStringUni(pv.pointerValue) ?? "";
            } finally {
                PropVariantClear(ref pv);
            }
        } finally {
            Marshal.ReleaseComObject(store);
        }
    }

    [DllImport("shell32.dll", CharSet = CharSet.Unicode, EntryPoint = "SHGetPropertyStoreFromParsingName")]
    static extern int SHGetPropertyStoreFromParsingNamePtr(
        string pszPath,
        IntPtr pbc,
        uint flags,
        ref Guid riid,
        out IntPtr ppv
    );

    [DllImport("ole32.dll")]
    static extern int PropVariantClear(ref PropVariant pvar);

    [ComImport]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    [Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")]
    interface IPropertyStore {
        [PreserveSig] int GetCount(out uint cProps);
        [PreserveSig] int GetAt(uint iProp, out PropertyKey pkey);
        [PreserveSig] int GetValue(ref PropertyKey key, out PropVariant pv);
        [PreserveSig] int SetValue(ref PropertyKey key, ref PropVariant pv);
        [PreserveSig] int Commit();
    }

    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    struct PropertyKey {
        public Guid fmtid;
        public uint pid;
    }

    [StructLayout(LayoutKind.Explicit)]
    struct PropVariant {
        [FieldOffset(0)] public ushort vt;
        [FieldOffset(8)] public IntPtr pointerValue;
    }
}
