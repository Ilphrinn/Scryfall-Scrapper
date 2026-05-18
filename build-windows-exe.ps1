$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

function Get-PythonCommand {
    $command = Get-Command py -ErrorAction SilentlyContinue
    if (-not $command) {
        throw "Le lanceur py n'est pas disponible. Installe Python 3.11+ depuis https://www.python.org/downloads/ en cochant l'option py launcher."
    }

    & py -3 -V | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "py ne trouve aucune installation Python. Installe Python 3.11+ depuis https://www.python.org/downloads/ puis relance ce script."
    }

    return @{ Command = "py"; Args = @("-3") }
}

$venvPath = Join-Path $ProjectRoot ".venv-build"
$venvPython = Join-Path $venvPath "Scripts\python.exe"
$exePath = Join-Path $ProjectRoot "dist\ScryfallArtworkDownloader.exe"
$iconPath = Join-Path $ProjectRoot "assets\logo.ico"
$manifestPath = Join-Path $ProjectRoot "windows_app.manifest"
$versionPath = Join-Path $ProjectRoot "windows_version_info.txt"
$buildPath = Join-Path $ProjectRoot "build"

function Stop-ExistingExecutable {
    $processes = Get-Process -Name "ScryfallArtworkDownloader" -ErrorAction SilentlyContinue
    foreach ($process in $processes) {
        Write-Host "Fermeture de l'executable deja lance: PID $($process.Id)"
        Stop-Process -Id $process.Id -Force
        try {
            Wait-Process -Id $process.Id -Timeout 10 -ErrorAction Stop
        }
        catch {
            Write-Host "Attente de fermeture depassee pour PID $($process.Id)"
        }
    }
}

function Remove-OldExecutable {
    if (-not (Test-Path -LiteralPath $exePath)) {
        return
    }

    for ($attempt = 1; $attempt -le 10; $attempt++) {
        try {
            Remove-Item -LiteralPath $exePath -Force
            return
        }
        catch {
            if ($attempt -eq 10) {
                throw "Impossible de remplacer $exePath. Ferme l'application si elle est ouverte, ferme l'explorateur sur le dossier dist si besoin, puis relance le build. Detail: $($_.Exception.Message)"
            }

            Write-Host "Executable encore verrouille, nouvel essai $attempt/10..."
            Start-Sleep -Milliseconds (300 * $attempt)
        }
    }
}

function Remove-BuildCache {
    if (Test-Path -LiteralPath $buildPath) {
        Remove-Item -LiteralPath $buildPath -Recurse -Force
    }
}

function Assert-ExecutableIcon {
    if (-not (Test-Path -LiteralPath $iconPath)) {
        throw "Icone Windows introuvable: $iconPath"
    }
}

function Assert-PackagingMetadata {
    if (-not (Test-Path -LiteralPath $manifestPath)) {
        throw "Manifeste Windows introuvable: $manifestPath"
    }
    if (-not (Test-Path -LiteralPath $versionPath)) {
        throw "Informations de version Windows introuvables: $versionPath"
    }
}

function Get-Sha256Hash {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path
    )

    if (Get-Command Get-FileHash -ErrorAction SilentlyContinue) {
        return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
    }

    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $sha256 = [System.Security.Cryptography.SHA256]::Create()
        try {
            $bytes = $sha256.ComputeHash($stream)
            return (($bytes | ForEach-Object { $_.ToString("x2") }) -join "").ToUpperInvariant()
        }
        finally {
            $sha256.Dispose()
        }
    }
    finally {
        $stream.Dispose()
    }
}

function Invoke-PyInstallerBuild {
    param(
        [switch] $SafeBuild
    )

    if ($SafeBuild) {
        Write-Host "Relance en mode compatibilite: sans metadata Windows avancee."
        $env:SCRYFALL_SAFE_BUILD = "1"
    }
    else {
        Remove-Item Env:\SCRYFALL_SAFE_BUILD -ErrorAction SilentlyContinue
    }

    & $venvPython -m PyInstaller --clean --noconfirm ScryfallArtworkDownloader.spec
    $script:PyInstallerExitCode = $LASTEXITCODE
    Remove-Item Env:\SCRYFALL_SAFE_BUILD -ErrorAction SilentlyContinue
}

if (-not (Test-Path -LiteralPath $venvPath)) {
    $python = Get-PythonCommand
    if ($python.Args.Count -gt 0) {
        & $python.Command @($python.Args + @("-m", "venv", $venvPath))
    }
    else {
        & $python.Command -m venv $venvPath
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Impossible de creer l'environnement virtuel Python."
    }
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Python du venv introuvable: $venvPython"
}

& $venvPython -m pip install --disable-pip-version-check -r requirements-build.txt
if ($LASTEXITCODE -ne 0) {
    throw "Installation des dependances impossible."
}
Stop-ExistingExecutable
Remove-OldExecutable
Remove-BuildCache
Assert-ExecutableIcon
Assert-PackagingMetadata
Invoke-PyInstallerBuild
if ($script:PyInstallerExitCode -ne 0) {
    Write-Host "Compilation standard impossible. Tentative de secours..."
    Remove-OldExecutable
    Remove-BuildCache
    Invoke-PyInstallerBuild -SafeBuild
}
if ($script:PyInstallerExitCode -ne 0) {
    throw "Compilation PyInstaller impossible, meme en mode compatibilite."
}

Unblock-File -LiteralPath $exePath -ErrorAction SilentlyContinue
$hash = Get-Sha256Hash -Path $exePath

Write-Host ""
Write-Host "Executable cree: $exePath"
Write-Host "SHA256: $hash"
