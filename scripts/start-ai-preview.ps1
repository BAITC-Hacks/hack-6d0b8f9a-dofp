param(
    [Parameter(Mandatory=$true)][string]$Snapshot,
    [string]$Python = 'python',
    [int]$Port = 8003
)
$ErrorActionPreference = 'Stop'
$snapshotDirectory = (Resolve-Path -LiteralPath $Snapshot).Path
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = ''
$env:MONEYGRAPH_AI_UI_ENABLED = 'true'
$env:MONEYGRAPH_AI_ENABLED = 'true'
$env:MONEYGRAPH_AI_ALLOW_EXTERNAL = 'true'
$env:MONEYGRAPH_AI_PROVIDER = 'openai'
Write-Host 'AI test server. Enter the OpenAI API key below; characters will be hidden.'
Write-Host 'The key stays in this terminal process and is not saved to a file.'
$aiSecureKey = Read-Host 'OpenAI API key' -AsSecureString
$aiKeyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($aiSecureKey)
try {
    $env:MONEYGRAPH_AI_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($aiKeyPointer)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($aiKeyPointer)
    $aiSecureKey.Dispose()
}
try {
    & $Python -m moneygraph.ai.check
    if ($LASTEXITCODE -ne 0) { throw 'AI configuration check failed. No external request was made.' }
    Write-Host "Open http://127.0.0.1:$Port and select the AI tab. Keep this terminal open."
    & $Python -m moneygraph.api --snapshot $snapshotDirectory --port $Port
} finally {
    Remove-Item Env:MONEYGRAPH_AI_API_KEY -ErrorAction SilentlyContinue
}
