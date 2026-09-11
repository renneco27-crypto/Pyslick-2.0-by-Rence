Step 1: Query NVIDIA API for Currently Active Models
Run this command in PowerShell to list all available active models under your NVIDIA API key:

PowerShell
$models = Invoke-RestMethod -Uri "https://integrate.api.nvidia.com/v1/models" -Headers @{ Authorization = "Bearer $env:NVIDIA_API_KEY" }
$models.data | Select-Object -Property id | Where-Object { $_.id -match "deepseek|llama
