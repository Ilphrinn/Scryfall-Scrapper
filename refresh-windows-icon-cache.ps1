$ErrorActionPreference = "Stop"

Write-Host "Fermeture temporaire de l'explorateur Windows..."
Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue

$cacheFiles = @(
    "$env:LOCALAPPDATA\IconCache.db",
    "$env:LOCALAPPDATA\Microsoft\Windows\Explorer\iconcache*",
    "$env:LOCALAPPDATA\Microsoft\Windows\Explorer\thumbcache*"
)

foreach ($pattern in $cacheFiles) {
    Get-ChildItem -Path $pattern -Force -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
}

Write-Host "Redemarrage de l'explorateur Windows..."
Start-Process explorer.exe

Write-Host "Cache d'icones rafraichi. Si besoin, deplace ou renomme l'exe pour forcer Windows a relire l'icone."
