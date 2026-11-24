# 用内网自签名证书打 Windows 安装包。
# 流程：先打 unpacked 目录 → 签主程序 → 用已签名的目录生成 NSIS → 签安装包。
# 私钥密码以 DPAPI 加密文件保存，脚本只在当前进程里解密成参数，不落盘、不回显。
# 不使用 electron-builder 内置签名（它会临时下载 winCodeSign，内网常常下不动）。
$desktop = Split-Path -Parent $PSScriptRoot
$code = Split-Path -Parent $desktop
$pfx = Join-Path $code "var\certs\ops-agent-codesign.pfx"
$passFile = Join-Path $code "var\certs\codesign.pass.dat"
$release = Join-Path $desktop "release"

if (-not (Test-Path -LiteralPath $pfx)) {
  throw "缺少 PFX：$pfx。先运行桌面证书生成步骤。"
}
if (-not (Test-Path -LiteralPath $passFile)) {
  throw "缺少密码密文：$passFile。"
}

$signtool = Get-ChildItem -LiteralPath (Join-Path $env:LOCALAPPDATA "electron-builder\Cache\winCodeSign") `
  -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
  Where-Object { $_.FullName -match 'windows-10\\x64' } |
  Select-Object -First 1 -ExpandProperty FullName
if (-not $signtool) { throw "找不到 signtool.exe 缓存；先跑一次 electron-builder 让它下载 winCodeSign。" }

$secure = ConvertTo-SecureString -String ((Get-Content -LiteralPath $passFile -Raw).Trim())
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
$pushed = $false
try {
  $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
  Remove-Item Env:\CSC_LINK -ErrorAction SilentlyContinue
  Remove-Item Env:\CSC_KEY_PASSWORD -ErrorAction SilentlyContinue

  Push-Location $desktop
  $pushed = $true

  Write-Output "== 1/3 package unpacked =="
  npm run pack
  if ($LASTEXITCODE -ne 0) { throw "electron-builder --dir 退出码 $LASTEXITCODE" }

  Write-Output "== 2/3 sign app exe =="
  & $signtool sign /f $pfx /p $plain /fd sha256 (Join-Path $release "win-unpacked\OpsAgent.exe")
  if ($LASTEXITCODE -ne 0) { throw "签名主程序失败：$LASTEXITCODE" }

  Write-Output "== 3/3 build installer from signed dir =="
  npm run dist:from-unpacked
  if ($LASTEXITCODE -ne 0) { throw "生成安装包失败：$LASTEXITCODE" }

  & $signtool sign /f $pfx /p $plain /fd sha256 (Join-Path $release "OpsAgent-0.1.0-setup.exe")
  if ($LASTEXITCODE -ne 0) { throw "签名安装包失败：$LASTEXITCODE" }

  # 安装包签名后哈希已变，旧的 blockmap 不再匹配；不做自动更新就删掉，避免误用。
  Get-ChildItem -LiteralPath $release -Filter "*.blockmap" -ErrorAction SilentlyContinue | Remove-Item -Force

  Write-Output "== verify =="
  Get-AuthenticodeSignature -LiteralPath (Join-Path $release "OpsAgent-0.1.0-setup.exe") |
    Select-Object Status, StatusMessage, @{n="Signer";e={$_.SignerCertificate.Subject}}
}
finally {
  if ($bstr) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
  Remove-Item Env:\CSC_LINK -ErrorAction SilentlyContinue
  Remove-Item Env:\CSC_KEY_PASSWORD -ErrorAction SilentlyContinue
  if ($pushed) { Pop-Location }
}
