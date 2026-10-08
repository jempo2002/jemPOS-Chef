# Aplica db/schema.sql y todas las migraciones de migrations/ (en orden de
# nombre) a la base MySQL de Railway, desde Windows.
#
#   .\scripts\migrar_railway.ps1
#
# Pide la MYSQL_PUBLIC_URL del servicio MySQL en Railway (Variables >
# MYSQL_PUBLIC_URL, la que tiene ...proxy.rlwy.net) sin mostrarla en pantalla.
# Las credenciales solo viven en esta ventana mientras corre: no toca el .env.
#
# Se puede correr varias veces: run_migration.py trata como hecho lo que ya
# existe. Para antes de seguir si una migracion falla.

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz

$segura = Read-Host "Pega la MYSQL_PUBLIC_URL de Railway" -AsSecureString
$url = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($segura))
$url = $url.Trim()
if (-not $url.StartsWith("mysql://")) {
    throw "Eso no parece una MYSQL_PUBLIC_URL (debe empezar por mysql://)."
}

$uri = [Uri]$url
$usuario, $clave = $uri.UserInfo.Split(":", 2)

$python = Join-Path $raiz ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

$env:DB_HOST = $uri.Host
$env:DB_PORT = [string]$uri.Port
$env:DB_USER = [Uri]::UnescapeDataString($usuario)
$env:DB_PASSWORD = [Uri]::UnescapeDataString($clave)
$env:DB_NAME = $uri.AbsolutePath.TrimStart("/")

try {
    $archivos = @("db\schema.sql") + (Get-ChildItem migrations\*.sql | Sort-Object Name | ForEach-Object { "migrations\$($_.Name)" })
    foreach ($archivo in $archivos) {
        Write-Host "==> $archivo" -ForegroundColor Cyan
        & $python scripts\run_migration.py $archivo
        if ($LASTEXITCODE -ne 0) { throw "Fallo $archivo. No se aplicaron las siguientes." }
    }
    Write-Host "Listo: base de Railway al dia." -ForegroundColor Green
}
finally {
    Remove-Item Env:DB_HOST, Env:DB_PORT, Env:DB_USER, Env:DB_PASSWORD, Env:DB_NAME -ErrorAction SilentlyContinue
}
