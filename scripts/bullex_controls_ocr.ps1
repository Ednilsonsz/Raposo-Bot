$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime] | Out-Null
$asTaskMethod = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 })[0]
function AwaitResult($Operation,$ResultType) { $taskObject=$asTaskMethod.MakeGenericMethod($ResultType).Invoke($null,@($Operation)); $taskObject.Wait(); $taskObject.Result }
$fileObject=AwaitResult ([Windows.Storage.StorageFile]::GetFileFromPathAsync($env:BULLEX_OCR_IMAGE)) ([Windows.Storage.StorageFile])
$streamObject=AwaitResult ($fileObject.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoderObject=AwaitResult ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($streamObject)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmapObject=AwaitResult ($decoderObject.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engineObject=[Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
$resultObject=AwaitResult ($engineObject.RecognizeAsync($bitmapObject)) ([Windows.Media.Ocr.OcrResult])
$rows=@(foreach($line in $resultObject.Lines){foreach($word in $line.Words){ @{text=$word.Text;x=$word.BoundingRect.X;y=$word.BoundingRect.Y;width=$word.BoundingRect.Width;height=$word.BoundingRect.Height} }})
ConvertTo-Json -InputObject $rows -Compress
