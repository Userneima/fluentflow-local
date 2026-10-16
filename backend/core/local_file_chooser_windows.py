"""The Windows file and folder dialogs, for ``local_file_chooser``.

The reason for a system dialog is the same as on macOS: it answers with a real
path, so the recording is read where it lies and its outputs go beside it. Before
this, Windows fell back to the browser upload, which copies the file into the
store and has no "beside the original" to write into.

Windows PowerShell 5.1 ships with every supported Windows and can show the
standard dialogs through Windows Forms; nothing has to be installed.

- The dialog is owned by an invisible topmost window. A dialog started from a
  background process otherwise opens behind the browser, as it did on macOS.
- Everything the user or a folder name supplies (the prompt, the starting
  folder) is passed in environment variables, never written into the script, so
  no quote or backslash in it can change what the script does.
- The paths come back as UTF-8, one per line. A cancelled dialog prints nothing.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from backend.core.media_intake import AUDIO_SUFFIXES, VIDEO_SUFFIXES

_ENV_PROMPT = "FFDIALOG_PROMPT"
_ENV_START = "FFDIALOG_START"
_ENV_MULTIPLE = "FFDIALOG_MULTIPLE"
_ENV_FILTER = "FFDIALOG_FILTER"
_ENV_PICKER = "FFDIALOG_PICKER"
_ENV_OK = "FFDIALOG_OK"
_ENV_NAME = "FFDIALOG_NAME"

_OWNER = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::EnableVisualStyles()
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.Opacity = 0
$owner.Show()
$owner.Activate()
"""

_FILES = _OWNER + r"""
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = $env:FFDIALOG_PROMPT
$dialog.Filter = $env:FFDIALOG_FILTER
$dialog.Multiselect = ($env:FFDIALOG_MULTIPLE -eq '1')
if ($env:FFDIALOG_START) { $dialog.InitialDirectory = $env:FFDIALOG_START }
if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
    foreach ($name in $dialog.FileNames) { [Console]::Out.WriteLine($name) }
}
$owner.Close()
"""

# The folder dialog is the Explorer-style one (IFileOpenDialog with
# FOS_PICKFOLDERS): an address bar, recent places, a path can be pasted. Windows
# Forms only offers FolderBrowserDialog, a bare tree that has to be clicked open
# level by level; it stays as the fallback if the type below cannot be built.
_FOLDER_PICKER_CS = r"""
using System;
using System.Runtime.InteropServices;
public static class FluentFlowFolderPicker {
    [ComImport, Guid("DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7")] class FileOpenDialog {}
    [ComImport, Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IShellItem {
        void BindToHandler(IntPtr pbc, ref Guid bhid, ref Guid riid, out IntPtr ppv);
        void GetParent(out IShellItem ppsi);
        void GetDisplayName(uint sigdn, [MarshalAs(UnmanagedType.LPWStr)] out string name);
        void GetAttributes(uint mask, out uint attributes);
        void Compare(IShellItem psi, uint hint, out int order);
    }
    [ComImport, Guid("42f85136-db7e-439c-85f1-e4075d135fc8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IFileDialog {
        [PreserveSig] int Show(IntPtr parent);
        void SetFileTypes(uint count, IntPtr specs);
        void SetFileTypeIndex(uint index);
        void GetFileTypeIndex(out uint index);
        void Advise(IntPtr events, out uint cookie);
        void Unadvise(uint cookie);
        void SetOptions(uint options);
        void GetOptions(out uint options);
        void SetDefaultFolder(IShellItem item);
        void SetFolder(IShellItem item);
        void GetFolder(out IShellItem item);
        void GetCurrentSelection(out IShellItem item);
        void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string name);
        void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string name);
        void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string title);
        void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string label);
        void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string label);
        void GetResult(out IShellItem item);
    }
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    static extern void SHCreateItemFromParsingName(string path, IntPtr pbc, ref Guid riid, out IShellItem item);

    public static string Pick(IntPtr owner, string title, string start, string okLabel, string nameLabel) {
        IFileDialog dialog = (IFileDialog)new FileOpenDialog();
        uint options;
        dialog.GetOptions(out options);
        dialog.SetOptions(options | 0x20 | 0x40);  // FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM
        if (!string.IsNullOrEmpty(title)) dialog.SetTitle(title);
        if (!string.IsNullOrEmpty(okLabel)) dialog.SetOkButtonLabel(okLabel);
        if (!string.IsNullOrEmpty(nameLabel)) dialog.SetFileNameLabel(nameLabel);
        if (!string.IsNullOrEmpty(start)) {
            try {
                Guid shellItem = typeof(IShellItem).GUID;
                IShellItem folder;
                SHCreateItemFromParsingName(start, IntPtr.Zero, ref shellItem, out folder);
                dialog.SetFolder(folder);
            } catch (Exception) { }
        }
        if (dialog.Show(owner) != 0) return null;  // cancelled
        IShellItem result;
        dialog.GetResult(out result);
        string path;
        result.GetDisplayName(0x80058000, out path);  // SIGDN_FILESYSPATH
        return path;
    }
}
"""

_FOLDER = _OWNER + r"""
$picked = $null
$modern = $true
try {
    Add-Type -TypeDefinition $env:FFDIALOG_PICKER -Language CSharp
} catch {
    $modern = $false
}
if ($modern) {
    $picked = [FluentFlowFolderPicker]::Pick($owner.Handle, $env:FFDIALOG_PROMPT, $env:FFDIALOG_START, $env:FFDIALOG_OK, $env:FFDIALOG_NAME)
} else {
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = $env:FFDIALOG_PROMPT
    $dialog.ShowNewFolderButton = $false
    if ($env:FFDIALOG_START) { $dialog.SelectedPath = $env:FFDIALOG_START }
    if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { $picked = $dialog.SelectedPath }
}
if ($picked) { [Console]::Out.WriteLine($picked) }
$owner.Close()
"""


def powershell_path() -> str | None:
    found = shutil.which("powershell")
    if found:
        return found
    system_root = os.environ.get("SYSTEMROOT") or r"C:\Windows"
    fallback = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(fallback) if fallback.is_file() else None


def media_filter() -> str:
    """The dialog's file filter: the recordings the product can take, then all files."""
    patterns = ";".join(f"*{suffix}" for suffix in sorted(VIDEO_SUFFIXES | AUDIO_SUFFIXES))
    return f"音视频文件|{patterns}|所有文件|*.*"


def _encoded(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def command(binary: str, script: str) -> list[str]:
    return [binary, "-NoProfile", "-NonInteractive", "-STA", "-ExecutionPolicy", "Bypass",
            "-EncodedCommand", _encoded(script)]


def _start_folder(start_in: Path | str | None) -> str:
    if not start_in:
        return ""
    folder = Path(start_in).expanduser()
    return str(folder) if folder.is_dir() else ""


def run_files_dialog(binary: str, *, prompt: str, allow_multiple: bool, start_in: Path | str | None,
                     execute: Any, timeout: int) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, _ENV_PROMPT: prompt, _ENV_START: _start_folder(start_in),
           _ENV_MULTIPLE: "1" if allow_multiple else "0", _ENV_FILTER: media_filter()}
    return _run(execute, command(binary, _FILES), env, timeout)


def run_folder_dialog(binary: str, *, prompt: str, start_in: Path | str | None,
                      execute: Any, timeout: int) -> subprocess.CompletedProcess[str]:
    # The pick-folders mode labels its button and field from the process's own
    # resources, which came out English ("Select Folder") on a Chinese Windows.
    # They follow the language the page asked in.
    chinese = any("\u4e00" <= char <= "\u9fff" for char in prompt)
    env = {**os.environ, _ENV_PROMPT: prompt, _ENV_START: _start_folder(start_in),
           _ENV_PICKER: _FOLDER_PICKER_CS,
           _ENV_OK: "选择这个文件夹" if chinese else "Select folder",
           _ENV_NAME: "文件夹：" if chinese else "Folder:"}
    return _run(execute, command(binary, _FOLDER), env, timeout)


def _run(execute: Any, argv: list[str], env: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    return execute(
        argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, check=False, env=env, stdin=subprocess.DEVNULL,
    )


__all__ = ["command", "media_filter", "powershell_path", "run_files_dialog", "run_folder_dialog"]
