$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
node scripts/check-native.mjs
if ($LASTEXITCODE -ne 0) { throw 'Please configure the deployed HTTPS API first' }
npm run build
if ($LASTEXITCODE -ne 0) { throw 'Web build failed' }
npx cap sync android
if ($LASTEXITCODE -ne 0) { throw 'Android sync failed' }
Push-Location android
try {
    & './gradlew.bat' assembleDebug
    if ($LASTEXITCODE -ne 0) { throw 'Android build failed' }
} finally { Pop-Location }
Write-Output 'Installable debug APK: android/app/build/outputs/apk/debug/app-debug.apk'
