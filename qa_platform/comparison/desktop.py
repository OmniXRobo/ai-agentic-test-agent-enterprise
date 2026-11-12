"""Desktop (Windows) and remote-desktop sources for the Data Comparison Agent.

Desktop applications are read through Windows UI Automation (System.Windows.Automation, part of .NET on Windows),
called through PowerShell, so nothing extra is installed. Only windows registered for a comparison application are
touched: the window must match the registered process name and/or title pattern, and nothing is clicked or typed.

Remote desktop (mstsc.exe, Citrix, VMware Horizon) windows show pixels only; UI Automation cannot see inside them.
For those the window is captured to a PNG and read with OCR (``ocr.py``) - the results carry OCR confidence, and
low-confidence values are compared as REVIEW, never as PASS or FAIL.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from ..errors import QAError

REMOTE_PROCESSES = {"mstsc", "msrdc", "wfica32", "cdviewer", "vmware-view", "vmware-remotemks"}

LIST_WINDOWS = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = [System.Windows.Automation.Condition]::TrueCondition
$out = @()
foreach ($w in $root.FindAll([System.Windows.Automation.TreeScope]::Children, $cond)) {
  try {
    $p = Get-Process -Id $w.Current.ProcessId -ErrorAction SilentlyContinue
    $r = $w.Current.BoundingRectangle
    $out += @{ handle = [int64]$w.Current.NativeWindowHandle; title = $w.Current.Name; process = $p.ProcessName;
               class = $w.Current.ClassName; box = @($r.X, $r.Y, $r.Width, $r.Height) }
  } catch {}
}
$out | ConvertTo-Json -Depth 4 -Compress
"""

READ_WINDOW = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$A = [System.Windows.Automation.AutomationElement]
$win = $A::FromHandle([IntPtr][int64]$env:QA_HWND)
$T = [System.Windows.Automation.ControlType]
$all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
$tables = @(); $fields = @(); $n = 0
foreach ($e in $all) {
  $n++; if ($n -gt 20000) { break }
  $ct = $e.Current.ControlType
  if ($ct -eq $T::DataGrid -or $ct -eq $T::Table -or $ct -eq $T::List) {
    $rows = @(); $headers = @()
    $gp = $null
    try { $gp = $e.GetCurrentPattern([System.Windows.Automation.GridPattern]::Pattern) } catch {}
    if ($gp) {
      try { $tp = $e.GetCurrentPattern([System.Windows.Automation.TablePattern]::Pattern); foreach ($h in $tp.Current.GetColumnHeaders()) { $headers += $h.Current.Name } } catch {}
      for ($r = 0; $r -lt [Math]::Min($gp.Current.RowCount, 5000); $r++) {
        $row = @()
        for ($c = 0; $c -lt $gp.Current.ColumnCount; $c++) {
          $cell = $gp.GetItem($r, $c); $v = $cell.Current.Name
          try { $vp = $cell.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern); $v = $vp.Current.Value } catch {}
          $row += $v
        }
        $rows += ,$row
      }
    } else {
      foreach ($item in $e.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)) {
        $cells = @(); foreach ($c in $item.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)) { $cells += $c.Current.Name }
        if ($cells.Count -eq 0) { $cells = @($item.Current.Name) }
        $rows += ,$cells
      }
    }
    $tables += @{ name = $e.Current.Name; automation_id = $e.Current.AutomationId; headers = $headers; rows = $rows }
  } elseif ($ct -eq $T::Edit -or $ct -eq $T::Text -or $ct -eq $T::ComboBox -or $ct -eq $T::CheckBox) {
    if ($e.Current.IsPassword) { continue }
    $v = $e.Current.Name
    try { $vp = $e.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern); $v = $vp.Current.Value } catch {}
    $fields += @{ name = $e.Current.Name; automation_id = $e.Current.AutomationId; type = $ct.ProgrammaticName; value = $v }
  }
}
@{ title = $win.Current.Name; tables = $tables; fields = $fields } | ConvertTo-Json -Depth 8 -Compress
"""

CAPTURE = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public class QaWin { [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint flags); }
"@
$h = [IntPtr][int64]$env:QA_HWND
$r = New-Object QaWin+RECT
[QaWin]::GetWindowRect($h, [ref]$r) | Out-Null
$w = $r.R - $r.L; $hh = $r.B - $r.T
$bmp = New-Object System.Drawing.Bitmap $w, $hh
$g = [System.Drawing.Graphics]::FromImage($bmp)
$dc = $g.GetHdc(); $ok = [QaWin]::PrintWindow($h, $dc, 2); $g.ReleaseHdc($dc)
if (-not $ok) { $g.CopyFromScreen($r.L, $r.T, 0, 0, $bmp.Size) }
$bmp.Save($env:QA_OUT, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
"""


def available() -> bool:
    return os.name == "nt"


def _ps(script: str, env: dict[str, str] | None = None, timeout: int = 120) -> str:
    if not available():
        raise QAError("DESKTOP_UNAVAILABLE", "Desktop and remote-desktop sources need Windows.", status=503)
    proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                           script], capture_output=True, text=True, timeout=timeout, env=dict(os.environ, **(env or {})),
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if proc.returncode != 0:
        raise QAError("DESKTOP_FAILED", "Windows UI Automation did not answer.", status=500,
                      details=(proc.stderr or proc.stdout)[-600:])
    return proc.stdout


def windows() -> list[dict[str, Any]]:
    data = json.loads(_ps(LIST_WINDOWS) or "[]")
    items = data if isinstance(data, list) else [data]
    for w in items:
        w["remote"] = str(w.get("process") or "").lower() in REMOTE_PROCESSES
    return [w for w in items if w.get("title")]


def matches(window: dict[str, Any], config: dict[str, Any]) -> bool:
    """A window may be read only when it matches the registered application (process and/or title pattern)."""
    proc = str(config.get("process") or "").lower().removesuffix(".exe")
    pattern = config.get("title_pattern")
    if not proc and not pattern:
        return False
    if proc and str(window.get("process") or "").lower() != proc:
        return False
    if pattern:
        try:
            return re.search(pattern, str(window.get("title") or ""), re.I) is not None
        except re.error:
            return str(pattern).lower() in str(window.get("title") or "").lower()
    return True


def find_window(config: dict[str, Any]) -> dict[str, Any]:
    hits = [w for w in windows() if matches(w, config)]
    if not hits:
        raise QAError("DESKTOP_WINDOW_NOT_FOUND", "The registered application window is not open. Open it and sign "
                      "in, then try again.", status=404)
    return hits[0]


def read_window(window: dict[str, Any]) -> dict[str, Any]:
    if window.get("remote"):
        raise QAError("DESKTOP_REMOTE", "This is a remote-desktop window: read it with a screen capture and OCR.",
                      status=422)
    data = json.loads(_ps(READ_WINDOW, {"QA_HWND": str(int(window["handle"]))}, timeout=300) or "{}")
    for t in data.get("tables") or []:
        rows = t.get("rows") or []
        headers = t.get("headers") or (rows[0] if rows else [])
        body = rows if t.get("headers") else rows[1:]
        t["records"] = [{"fields": {str(h): {"value": v, "method": "uia", "confidence": 1.0}
                                    for h, v in zip(headers, r if isinstance(r, list) else [r])}} for r in body]
    return data


def capture(window: dict[str, Any], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    _ps(CAPTURE, {"QA_HWND": str(int(window["handle"])), "QA_OUT": str(out)})
    if not out.is_file():
        raise QAError("DESKTOP_CAPTURE_FAILED", "The window could not be captured.", status=500)
    return out
