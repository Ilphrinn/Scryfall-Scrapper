$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$venvPath    = Join-Path $ProjectRoot ".venv-build"
$venvPython  = Join-Path $venvPath "Scripts\python.exe"
$exePath     = Join-Path $ProjectRoot "dist\ScryfallArtworkDownloader.exe"
$iconPath    = Join-Path $ProjectRoot "assets\logo.ico"
$manifestPath = Join-Path $ProjectRoot "windows_app.manifest"
$versionPath  = Join-Path $ProjectRoot "windows_version_info.txt"
$buildPath   = Join-Path $ProjectRoot "build"

# --- Détection Python ---
function Get-PythonCommand {
    $cmd = Get-Command py -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw "Le lanceur py n'est pas disponible. Installe Python 3.11+ depuis https://www.python.org/downloads/ en cochant 'py launcher'."
    }
    & py -3 -V | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "py ne trouve aucune installation Python 3. Installe Python 3.11+ puis relance ce script."
    }
    return @{ Command = "py"; Args = @("-3") }
}

# --- Gestion de l'exécutable existant ---
function Stop-ExistingExecutable {
    $processes = Get-Process -Name "ScryfallArtworkDownloader" -ErrorAction SilentlyContinue
    foreach ($p in $processes) {
        Write-Host "Fermeture du processus en cours : PID $($p.Id)"
        Stop-Process -Id $p.Id -Force
        try { Wait-Process -Id $p.Id -Timeout 10 -ErrorAction Stop }
        catch { Write-Host "Délai d'attente dépassé pour PID $($p.Id)" }
    }
}

function Remove-OldExecutable {
    if (-not (Test-Path -LiteralPath $exePath)) { return }
    for ($i = 1; $i -le 10; $i++) {
        try {
            Remove-Item -LiteralPath $exePath -Force
            return
        } catch {
            if ($i -eq 10) {
                throw "Impossible de remplacer $exePath. Ferme l'application et l'Explorateur sur le dossier dist, puis relance. Détail : $($_.Exception.Message)"
            }
            Write-Host "Exécutable verrouillé, tentative $i/10..."
            Start-Sleep -Milliseconds (300 * $i)
        }
    }
}

function Remove-BuildCache {
    if (Test-Path -LiteralPath $buildPath) {
        Remove-Item -LiteralPath $buildPath -Recurse -Force
    }
}

# --- Vérifications des assets ---
function Assert-ExecutableIcon {
    if (-not (Test-Path -LiteralPath $iconPath)) {
        throw "Icône introuvable : $iconPath"
    }
}

function Assert-PackagingMetadata {
    if (-not (Test-Path -LiteralPath $manifestPath)) {
        throw "Manifeste Windows introuvable : $manifestPath"
    }
    if (-not (Test-Path -LiteralPath $versionPath)) {
        throw "Informations de version introuvables : $versionPath"
    }
}

# --- Compilation PyInstaller ---
function Invoke-PyInstallerBuild {
    param([switch] $SafeBuild)
    if ($SafeBuild) {
        Write-Host "Mode compatibilité : métadonnées Windows avancées désactivées."
        $env:SCRYFALL_SAFE_BUILD = "1"
    } else {
        Remove-Item Env:\SCRYFALL_SAFE_BUILD -ErrorAction SilentlyContinue
    }
    & $venvPython -m PyInstaller --clean --noconfirm ScryfallArtworkDownloader.spec
    $script:PyInstallerExitCode = $LASTEXITCODE
    Remove-Item Env:\SCRYFALL_SAFE_BUILD -ErrorAction SilentlyContinue
}

# --- Création du venv (seulement si absent) ---
if (-not (Test-Path -LiteralPath $venvPython)) {
    $python = Get-PythonCommand
    Write-Host "Création de l'environnement virtuel..."
    if ($python.Args.Count -gt 0) {
        & $python.Command @($python.Args + @("-m", "venv", $venvPath))
    } else {
        & $python.Command -m venv $venvPath
    }
    if ($LASTEXITCODE -ne 0) { throw "Impossible de créer l'environnement virtuel Python." }
}

Write-Host "Installation des dépendances..."
& $venvPython -m pip install --quiet --disable-pip-version-check -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw "Installation des dépendances impossible." }

Stop-ExistingExecutable
Remove-OldExecutable
Remove-BuildCache
Assert-ExecutableIcon
Assert-PackagingMetadata

Write-Host "Compilation..."
Invoke-PyInstallerBuild
if ($script:PyInstallerExitCode -ne 0) {
    Write-Host "Compilation standard échouée. Tentative en mode compatibilité..."
    Remove-OldExecutable
    Remove-BuildCache
    Invoke-PyInstallerBuild -SafeBuild
}
if ($script:PyInstallerExitCode -ne 0) {
    throw "Compilation PyInstaller impossible, même en mode compatibilité."
}

Unblock-File -LiteralPath $exePath -ErrorAction SilentlyContinue

$hash = (Get-FileHash -LiteralPath $exePath -Algorithm SHA256).Hash
$size = "{0:N1} Mo" -f ((Get-Item -LiteralPath $exePath).Length / 1MB)

Write-Host ""
Write-Host "Exécutable créé : $exePath"
Write-Host "Taille  : $size"
Write-Host "SHA256  : $hash"
