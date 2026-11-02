# Set up a Watchman server on a Windows machine with Docker Desktop.
#
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#
# It creates .env from .env.example if there is none, asks how people sign in to the portal
# (Tailscale or your own OIDC provider) and for the values that mode needs, fetches and verifies
# the models, and starts the stack with `docker compose up -d --build`. Run it again to change
# answers: Enter keeps what is already in .env. Nothing is sent anywhere except the model
# downloads listed in models\manifest.json and your own identity provider.

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$EnvFile = Join-Path $Root '.env'
$Utf8NoBom = New-Object System.Text.UTF8Encoding $false

function Read-EnvLines {
    if (Test-Path $EnvFile) { return @([System.IO.File]::ReadAllLines($EnvFile)) }
    return @()
}

function Write-EnvLines($lines) {
    # LF and no byte order mark, so docker compose reads the first key correctly.
    [System.IO.File]::WriteAllText($EnvFile, (($lines -join "`n") + "`n"), $Utf8NoBom)
}

function Get-EnvValue($key) {
    $value = ''
    foreach ($line in Read-EnvLines) {
        if ($line.StartsWith("$key=")) { $value = $line.Substring($key.Length + 1) }
    }
    return $value
}

function Set-EnvValue($key, $value) {
    $out = New-Object System.Collections.Generic.List[string]
    $done = $false
    foreach ($line in Read-EnvLines) {
        $bare = $line -replace '^#\s*', ''
        if ($bare.StartsWith("$key=")) {
            if (-not $done) { $out.Add("$key=$value"); $done = $true }
            continue
        }
        $out.Add($line)
    }
    if (-not $done) { $out.Add("$key=$value") }
    Write-EnvLines $out
}

function Disable-EnvValue($key) {
    $out = foreach ($line in Read-EnvLines) { if ($line.StartsWith("$key=")) { "# $line" } else { $line } }
    Write-EnvLines @($out)
}

function Ask($prompt, $key, $default = '') {
    $current = Get-EnvValue $key
    if (-not $current) { $current = $default }
    $shown = if ($current) { "$prompt [$current]" } else { $prompt }
    $reply = Read-Host $shown
    if (-not $reply) { $reply = $current }
    Set-EnvValue $key $reply
}

function Ask-Secret($prompt, $key, [switch]$Optional) {
    $current = Get-EnvValue $key
    if ($current.EndsWith('...')) { $current = '' }   # the .env.example placeholder
    while ($true) {
        $shown = if ($current) { "$prompt (Enter keeps the current one)" } else { $prompt }
        $secure = Read-Host $shown -AsSecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try { $reply = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
        if (-not $reply) { $reply = $current }
        if ($reply -or $Optional) { break }
        Write-Host 'This one is needed.'
    }
    Set-EnvValue $key $reply
}

# 1. Prerequisites and .env

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker is not installed. Install Docker Desktop first.'
}
docker compose version | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'docker compose is missing. Update Docker Desktop.' }
if (-not (Test-Path $EnvFile)) {
    Write-EnvLines @([System.IO.File]::ReadAllLines((Join-Path $Root '.env.example')))
    Write-Host 'Created .env from .env.example.'
}

# 2. How people sign in

Write-Host ''
Write-Host 'How will people sign in to the portal?'
Write-Host '  t  Tailscale: everyone opens it on your tailnet; no passwords anywhere (the default).'
Write-Host '  o  OIDC: your own identity provider, such as Pocket ID, behind your own reverse proxy.'
$currentMode = if ((Get-EnvValue 'COMPOSE_FILE') -like '*compose.oidc.yaml*') { 'o' } else { 't' }
$mode = Read-Host "Tailscale or OIDC? (t/o) [$currentMode]"
if (-not $mode) { $mode = $currentMode }
$portal = ''

switch -Regex ($mode) {
    '^(t|tailscale)$' {
        $mode = 'tailscale'
        Disable-EnvValue 'COMPOSE_FILE'
        Disable-EnvValue 'COMPOSE_PATH_SEPARATOR'
        Write-Host ''
        Write-Host 'Make an auth key in the Tailscale admin console (Settings, Keys): reusable, pre-authorized,'
        Write-Host 'tagged if your tailnet policy uses tags.'
        Ask-Secret 'Tailscale auth key (tskey-auth-...)' 'TS_AUTHKEY'
        Ask 'Node name on the tailnet' 'WATCHMAN_NODE_NAME' 'watchman'
        Ask 'Extra tailscale up flags, for example --advertise-tags=tag:watchman (Enter for none)' 'TS_EXTRA_ARGS'
    }
    '^(o|oidc)$' {
        $mode = 'oidc'
        Set-EnvValue 'COMPOSE_FILE' 'compose.yaml:deploy/compose.oidc.yaml'
        Set-EnvValue 'COMPOSE_PATH_SEPARATOR' ':'
        Write-Host ''
        Write-Host 'In your identity provider, create an OIDC client for Watchman with the callback URL'
        Write-Host '<portal address>/auth/callback, and allow only your household''s group to use it.'
        Write-Host 'The first person to sign in becomes Watchman''s admin.'
        Ask 'Issuer URL, for example https://id.example.home' 'WATCHMAN_OIDC_ISSUER'
        Ask 'Client ID' 'WATCHMAN_OIDC_CLIENT_ID'
        Ask-Secret 'Client secret (Enter for a public client)' 'WATCHMAN_OIDC_CLIENT_SECRET' -Optional
        $currentUrl = (Get-EnvValue 'WATCHMAN_OIDC_REDIRECT_URL') -replace '/auth/callback$', ''
        $shown = 'Portal address as your reverse proxy serves it, for example https://watchman.example.home'
        if ($currentUrl) { $shown = "$shown [$currentUrl]" }
        $portal = Read-Host $shown
        if (-not $portal) { $portal = $currentUrl }
        if (-not $portal) { throw 'The portal address is needed for the callback URL.' }
        $portal = $portal.TrimEnd('/')
        Set-EnvValue 'WATCHMAN_OIDC_REDIRECT_URL' "$portal/auth/callback"
        Ask 'Host address and port your reverse proxy forwards to' 'WATCHMAN_PUBLISH' '127.0.0.1:8030'
    }
    default { throw 'Answer t or o.' }
}

# 3. Models

Write-Host ''
Write-Host 'Fetching and verifying the models (models\manifest.json).'
Write-Host 'If you downloaded the watchman-models artifact from this repository''s Actions (models'
Write-Host 'workflow), unzip it and give the folder here. Otherwise press Enter.'
$artifact = Read-Host 'Artifact folder (Enter to skip)'
$fetchArgs = @()
if ($artifact) {
    if (-not (Test-Path $artifact -PathType Container)) { throw "$artifact is not a folder." }
    $fetchArgs = @('--from-artifact', (Resolve-Path $artifact).Path)
}
if (-not $env:GITHUB_TOKEN -and (Get-Command gh -ErrorAction SilentlyContinue)) {
    # GitHub sends anonymous downloads of the NudeNet release files to a sign-in page.
    try {
        $token = & gh auth token 2>$null
        if ($LASTEXITCODE -eq 0 -and $token) { $env:GITHUB_TOKEN = "$token".Trim() }
    } catch { }   # not signed in to gh: the NudeNet files may then need GITHUB_TOKEN or the artifact
}
$modelsDir = Join-Path $Root 'models'
if (Get-Command uv -ErrorAction SilentlyContinue) {
    & uv run --directory $modelsDir python fetch.py @fetchArgs
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 (Join-Path $modelsDir 'fetch.py') @fetchArgs
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python (Join-Path $modelsDir 'fetch.py') @fetchArgs
} else {
    throw 'Neither uv nor Python is installed; one of them runs models\fetch.py.'
}
$modelsOk = ($LASTEXITCODE -eq 0)

if (-not $modelsOk) {
    $names = 'nsfw-384.onnx', 'nsfw-384-int8.onnx', 'nudenet-320n.onnx', 'nudenet-640m.onnx', 'vit-base-nsfw-int8.onnx'
    $missing = @($names | Where-Object { -not (Test-Path (Join-Path $modelsDir "cache\$_")) })
    Write-Host ''
    if ($missing.Count) {
        Write-Host "Some model files are missing or did not verify: $($missing -join ', ')."
    } else {
        Write-Host 'Some model files did not verify; see the lines above.'
    }
    if ($missing -like 'nsfw-384*') {
        Write-Host 'The Marqo classifier files (nsfw-384.onnx and nsfw-384-int8.onnx) are not downloaded:'
        Write-Host 'they are exported from the published weights, which needs torch. Three ways to get them:'
        Write-Host '  1. Download the watchman-models artifact (Actions, models, a run started by hand),'
        Write-Host '     unzip it, and run this script again with that folder.'
        Write-Host '  2. Install the export toolchain and export here (a few GB of downloads):'
        Write-Host '       cd models; uv sync --group export; uv run python fetch.py --export'
        Write-Host '  3. Copy models\cache from another Watchman machine and run fetch.py to verify it.'
    }
    if ($missing -like 'nudenet*') {
        Write-Host 'The NudeNet files come from a GitHub release that GitHub serves only to signed-in'
        Write-Host 'clients: set GITHUB_TOKEN to a token from any GitHub account (or sign in with gh) and'
        Write-Host 'run this script again, or use the artifact folder.'
    }
    Write-Host 'The server starts without them, but devices cannot download a model it does not have,'
    Write-Host 'and without vit-base-nsfw-int8.onnx the second opinion is a stand-in that scores 0.'
    $goOn = Read-Host 'Start the server anyway? (y/n) [y]'
    if ($goOn -and $goOn -notmatch '^(y|yes)$') { Write-Host 'Stopped before starting the server.'; exit 1 }
}

# 4. Start

Write-Host ''
Write-Host 'Starting: docker compose up -d --build'
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { throw 'docker compose up failed; is Docker Desktop running?' }

Write-Host ''
if ($mode -eq 'tailscale') {
    $node = Get-EnvValue 'WATCHMAN_NODE_NAME'
    if (-not $node) { $node = 'watchman' }
    Write-Host "Done. Open https://$node.<your-tailnet>.ts.net from a device on your tailnet."
    Write-Host 'If the node waits for approval: docker compose exec tailscale tailscale status'
} else {
    Write-Host "Done. Point your reverse proxy at $(Get-EnvValue 'WATCHMAN_PUBLISH') and open $portal."
}
Write-Host 'The first person to open the portal becomes the admin and is walked through setup.'
