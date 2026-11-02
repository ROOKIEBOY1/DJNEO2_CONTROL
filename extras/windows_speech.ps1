param([Parameter(Mandatory=$true)][string]$GrammarPath, [switch]$Check, [string]$Emulate)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
function Emit($item) { [Console]::WriteLine(($item | ConvertTo-Json -Compress -Depth 4)) }
$engine = $null
try {
    Add-Type -AssemblyName System.Speech
    $info = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers() |
        Where-Object { $_.Culture.Name -eq 'zh-CN' } | Select-Object -First 1
    if ($null -eq $info) { throw 'No installed zh-CN System.Speech recognizer' }
    $engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine($info.Id)
    $phrases = [System.IO.File]::ReadAllText($GrammarPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    $choices = New-Object System.Speech.Recognition.Choices
    foreach ($phrase in $phrases) { $choices.Add([string]$phrase) }
    $builder = New-Object System.Speech.Recognition.GrammarBuilder
    $builder.Culture = $info.Culture
    $builder.Append($choices)
    $grammar = New-Object System.Speech.Recognition.Grammar($builder)
    $engine.LoadGrammar($grammar)
    Emit @{type='ready'; recognizer=$info.Id; culture=$info.Culture.Name; phrases=$phrases.Count}
    if ($Check) { exit 0 }
    if ($Emulate) {
        $result = $engine.EmulateRecognize($Emulate)
        if ($null -eq $result) { throw 'Emulated phrase did not match grammar' }
        Emit @{type='emulated'; text=$result.Text; confidence=$result.Confidence}
        exit 0
    }
    $engine.SetInputToDefaultAudioDevice()
    $engine.EndSilenceTimeout = [TimeSpan]::FromMilliseconds(300)
    $engine.EndSilenceTimeoutAmbiguous = [TimeSpan]::FromMilliseconds(500)
    Emit @{type='listening'}
    while ($true) {
        $result = $engine.Recognize([TimeSpan]::FromSeconds(5))
        if ($null -ne $result) {
            Emit @{type='recognized'; text=$result.Text; confidence=$result.Confidence;
                at_unix_ms=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()}
        }
    }
} catch {
    Emit @{type='error'; message=$_.Exception.Message}
    exit 1
} finally {
    if ($null -ne $engine) { $engine.Dispose() }
}
