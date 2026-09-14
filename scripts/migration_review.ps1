param(
    [Parameter(Mandatory = $true)][string]$PlanPath,
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"

if ($NonInteractive -or $env:SUBTITLE_EDIT_BAY_MIGRATION_AUTO_APPROVE -eq "1") {
    # Silent Installer runs already carry the explicit task selection made in
    # Inno Setup.  They must not open a second UI or wait for a desktop.
    exit 0
}

if (-not (Test-Path -LiteralPath $PlanPath -PathType Leaf)) {
    throw "Migration plan is missing: $PlanPath"
}

$plan = Get-Content -LiteralPath $PlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
$lines = New-Object System.Collections.Generic.List[string]
[void]$lines.Add("Subtitle Edit Bay - 初回移行の確認")
[void]$lines.Add("")
[void]$lines.Add("移行元: $($plan.source)")
[void]$lines.Add("移行先: $($plan.destination)")
[void]$lines.Add("")
[void]$lines.Add("旧フォルダーは変更・削除されません。旧 .venv は実行環境として再利用しません。")
$overwriteText = if ([bool]$plan.request.overwrite) { "明示指定済み（confirm必須）" } else { "行わない（既定）" }
[void]$lines.Add("上書き: $overwriteText")
[void]$lines.Add("cleanup: 自動実行しない。移行成功後に候補ごとの明示確認が必要です。")
[void]$lines.Add("")
[void]$lines.Add("引き継ぐ設定 / 差分:")
foreach ($item in @($plan.settings.items)) {
    $status = if ($item.status -eq "ready") { "適用" } else { "保留: $($item.reason)" }
    [void]$lines.Add("- $($item.relative_path): $status")
    foreach ($diff in @($item.diff)) {
        [void]$lines.Add("    差分: $diff")
    }
    if ($item.diagnostic) { [void]$lines.Add("    診断: $($item.diagnostic)") }
}
[void]$lines.Add("")
[void]$lines.Add("project / media / output の参照登録:")
if (@($plan.workspace_references).Count -eq 0) {
    [void]$lines.Add("- なし")
} else {
    foreach ($reference in @($plan.workspace_references)) { [void]$lines.Add("- $reference") }
}
[void]$lines.Add("")
[void]$lines.Add("cleanup候補（移行成功後のみ個別確認可能）:")
$reclaimable = [int64]$plan.cleanup.reclaimable_bytes
[void]$lines.Add("- 解放可能容量（概算）: $reclaimable bytes")
foreach ($entry in @($plan.cleanup.entries)) {
    if ($entry.cleanup_allowed -or $entry.state -eq "removable_after_success") {
        [void]$lines.Add("- $($entry.candidate_id): $($entry.state), $($entry.reclaimable_bytes) bytes")
    }
}
[void]$lines.Add("- project / media / output と旧root全体は削除対象外")
if (@($plan.diagnostics).Count -gt 0) {
    [void]$lines.Add("")
    [void]$lines.Add("診断:")
    foreach ($diagnostic in @($plan.diagnostics)) { [void]$lines.Add("- $diagnostic") }
}

Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName PresentationCore
$window = New-Object Windows.Window
$window.Title = "Subtitle Edit Bay - 初回移行"
$window.Width = 780
$window.Height = 640
$window.WindowStartupLocation = "CenterScreen"
$window.MinWidth = 620
$window.MinHeight = 480

$root = New-Object Windows.Controls.DockPanel
$root.Margin = 16
$text = New-Object Windows.Controls.TextBox
$text.MinHeight = 380
$text.VerticalContentAlignment = "Top"
$text.IsReadOnly = $true
$text.AcceptsReturn = $true
$text.TextWrapping = "Wrap"
$text.VerticalScrollBarVisibility = "Auto"
$text.HorizontalScrollBarVisibility = "Auto"
$text.FontFamily = New-Object Windows.Media.FontFamily("Consolas, MS Gothic")
$text.FontSize = 12
$text.Text = $lines -join [Environment]::NewLine
[Windows.Controls.DockPanel]::SetDock($text, "Top")
[void]$root.Children.Add($text)

$buttons = New-Object Windows.Controls.StackPanel
$buttons.Orientation = "Horizontal"
$buttons.HorizontalAlignment = "Right"
$buttons.Margin = "0,12,0,0"
$apply = New-Object Windows.Controls.Button
$apply.Content = "移行を適用"
$apply.MinWidth = 130
$apply.Margin = "0,0,8,0"
$cancel = New-Object Windows.Controls.Button
$cancel.Content = "キャンセル（後で再試行）"
$cancel.MinWidth = 180
[Windows.Controls.DockPanel]::SetDock($buttons, "Bottom")
[void]$buttons.Children.Add($apply)
[void]$buttons.Children.Add($cancel)
[void]$root.Children.Add($buttons)
$window.Content = $root

$script:approved = $false
$apply.Add_Click({ $script:approved = $true; $window.Close() })
$cancel.Add_Click({ $script:approved = $false; $window.Close() })
[void]$window.ShowDialog()
if ($script:approved) { exit 0 }
exit 3
