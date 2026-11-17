param([string]$OutputDirectory = (Join-Path $PSScriptRoot '..\dist'))
$ErrorActionPreference = 'Stop'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $compiler)) { throw '.NET Framework C# compiler not found; build on Windows with .NET Framework 4.x.' }
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$source = Join-Path $PSScriptRoot 'windows\Launcher.cs'
$target = Join-Path $OutputDirectory 'openmc-forge.exe'
$version = (Get-Content (Join-Path $PSScriptRoot '..\VERSION') -Raw).Trim()
if ($version -notmatch '^[0-9A-Za-z][0-9A-Za-z.+_-]*$') { throw 'Invalid VERSION.' }
$attributes = Join-Path $OutputDirectory 'launcher-version.cs'
'[assembly:System.Reflection.AssemblyInformationalVersion("' + $version + '")]' | Set-Content -Encoding ASCII $attributes
$arguments = @('/nologo', '/target:exe', '/platform:anycpu', '/optimize+', '/reference:System.Web.Extensions.dll', "/out:`"$target`"", "`"$source`"", "`"$attributes`"")
$process = Start-Process -FilePath $compiler -ArgumentList $arguments -Wait -PassThru -NoNewWindow
if ($process.ExitCode -ne 0) { throw 'Launcher compilation failed.' }
(Get-FileHash $target -Algorithm SHA256).Hash.ToLower() + '  openmc-forge.exe' | Set-Content -Encoding ASCII (Join-Path $OutputDirectory 'openmc-forge.exe.sha256')
@{version=$version;source_sha256=(Get-FileHash $source -Algorithm SHA256).Hash.ToLower();exe_sha256=(Get-FileHash $target -Algorithm SHA256).Hash.ToLower()} | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $OutputDirectory 'launcher-build.json')
Write-Output $target
