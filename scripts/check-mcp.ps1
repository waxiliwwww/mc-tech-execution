param([string]$Url = "http://127.0.0.1:8770/mcp")
# Windows PowerShell 5.1+; performs discovery only, no model or computation calls.
$ErrorActionPreference = "Stop"
$phase = "URL"
try {
    $endpoint = [Uri]$Url
    if ($endpoint.Scheme -notin @("http", "https") -or $endpoint.UserInfo) {
        throw "Use an HTTP(S) endpoint without credentials in the URL."
    }
    $headers = @{ Accept = "application/json, text/event-stream" }
    if ($env:MCP_AUTH_TOKEN) { $headers.Authorization = "Bearer " + $env:MCP_AUTH_TOKEN }
    function Send-Mcp($payload) {
        $body = $payload | ConvertTo-Json -Depth 30 -Compress
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -Method Post -Headers $headers `
            -ContentType "application/json" -Body $body -TimeoutSec 30
        if ($response.Headers["Mcp-Session-Id"]) {
            $headers["Mcp-Session-Id"] = $response.Headers["Mcp-Session-Id"]
        }
        if (-not $response.Content) { return $null }
        $text = [string]$response.Content
        if ($text.TrimStart().StartsWith("data:")) {
            $lines = @($text -split "`n" | Where-Object { $_.StartsWith("data:") })
            $text = (($lines | ForEach-Object { $_.Substring(5).Trim() }) -join "`n")
        }
        $value = $text | ConvertFrom-Json
        if ($value.error) { throw "MCP response contained an error; check service logs." }
        return $value
    }
    $phase = "initialize"
    $initialized = Send-Mcp @{ jsonrpc="2.0"; id=1; method="initialize"; params=@{
        protocolVersion="2025-03-26"; capabilities=@{}; clientInfo=@{name="mc-windows-check";version="1"}
    }}
    if (-not $initialized.result.protocolVersion) { throw "No negotiated protocol version." }
    $headers["MCP-Protocol-Version"] = $initialized.result.protocolVersion
    $null = Send-Mcp @{jsonrpc="2.0";method="notifications/initialized"}
    $phase = "tools/list"
    $listed = Send-Mcp @{jsonrpc="2.0";id=2;method="tools/list";params=@{}}
    $tools = @($listed.result.tools)
    foreach ($required in @("openmc_build_model", "openmc_import_model", "openmc_submit_job", "openmc_read_results")) {
        if ($required -notin $tools.name) { throw "Required tool not discovered: $required" }
    }
    [ordered]@{
        passed=$true; phase="discovery"; url=$Url; protocol=$initialized.result.protocolVersion
        tools=$tools.Count; model_requests=0; compute_submissions=0
        note="Windows HTTP/MCP discovery passed. DSH plugin activation and natural-language use are separate checks."
    } | ConvertTo-Json -Depth 5
} catch {
    # Never dump HTTP request headers or tokens on a failed connection.
    Write-Error "MCP check failed at $phase. Check URL, service, firewall and authentication; no model or computation was called."
    exit 1
}
