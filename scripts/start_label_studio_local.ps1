$ErrorActionPreference = 'Stop'

$labelStudioExecutable = 'D:\apps\label-studio-tavidm\venv\Scripts\label-studio.exe'
$labelStudioData = 'D:\apps\label-studio-tavidm\data'
$annotationWorkspace = 'D:\tavidm-annotation-workspace'

if (-not (Test-Path -LiteralPath $labelStudioExecutable -PathType Leaf)) {
    throw "Label Studio executable not found: $labelStudioExecutable"
}
if (-not (Test-Path -LiteralPath $annotationWorkspace -PathType Container)) {
    throw "Annotation workspace not found: $annotationWorkspace"
}

$env:LABEL_STUDIO_BASE_DATA_DIR = $labelStudioData
$env:LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED = 'true'
$env:LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT = $annotationWorkspace

Write-Host 'Starting local Label Studio at http://127.0.0.1:8080'
Write-Host "Annotation workspace: $annotationWorkspace"
& $labelStudioExecutable start --host 127.0.0.1 --port 8080 --no-browser
