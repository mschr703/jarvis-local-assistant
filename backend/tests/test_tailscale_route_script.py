"""Exercise the Serve rollback with a fake command, never the real Tailnet."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="Windows PowerShell required")
def test_tailscale_start_rolls_back_only_its_route_when_record_fails(tmp_path):
    root = Path(__file__).resolve().parents[2]
    helper = root / "scripts" / "tailscale_route.ps1"
    failed_record = tmp_path / "missing" / "record.json"
    good_record = tmp_path / "record.json"
    script = f"""
$ErrorActionPreference = 'Stop'
. '{helper}'
$script:endpoint = 'another-laptop.other-tail.ts.net:9443'
$script:routes = @{{ 'another-laptop.other-tail.ts.net:443' = 'http://127.0.0.1:8000' }}
$script:events = [System.Collections.Generic.List[string]]::new()
function tailscale {{
    $command = $args -join ' '
    if ($command -eq 'serve status --json') {{
        $web = @{{}}
        foreach ($route in $script:routes.GetEnumerator()) {{
            $web[$route.Key] = @{{Handlers=@{{'/'=@{{Proxy=$route.Value}}}}}}
        }}
        $global:LASTEXITCODE = 0
        return (@{{Web=$web}} | ConvertTo-Json -Depth 10 -Compress)
    }}
    if ($command -eq 'serve --bg --https=9443 http://127.0.0.1:8770') {{
        $script:routes[$script:endpoint] = 'http://127.0.0.1:8770'
        $script:events.Add('start')
        $global:LASTEXITCODE = 0
        return
    }}
    if ($command -eq 'serve --https=9443 off') {{
        $script:routes.Remove($script:endpoint)
        $script:events.Add('off')
        $global:LASTEXITCODE = 0
        return
    }}
    throw "Unexpected fake tailscale command: $command"
}}
try {{
    Start-JarvisServeRoute -TailHost 'another-laptop.other-tail.ts.net' `
        -HttpsPort 9443 -GatewayPid 12345 -RecordPath '{failed_record}'
    throw 'Missing record should fail'
}} catch {{
    if ($_.Exception.Message -match 'Missing record should fail') {{ throw }}
}}
if ($script:routes.ContainsKey($script:endpoint)) {{ throw 'Jarvis route survived rollback' }}
if ($script:routes['another-laptop.other-tail.ts.net:443'] -ne 'http://127.0.0.1:8000') {{
    throw 'Unrelated route changed'
}}
if (($script:events -join ',') -ne 'start,off') {{ throw 'Expected start and rollback' }}
Start-JarvisServeRoute -TailHost 'another-laptop.other-tail.ts.net' `
    -HttpsPort 9443 -GatewayPid 12345 -RecordPath '{good_record}'
$record = Get-Content -LiteralPath '{good_record}' -Raw | ConvertFrom-Json
if ($record.host -ne 'another-laptop.other-tail.ts.net' -or $record.port -ne 9443 `
    -or $record.gateway_pid -ne 12345) {{ throw 'Wrong startup record' }}
if ($script:routes[$script:endpoint] -ne 'http://127.0.0.1:8770') {{
    throw 'Successful start did not keep route'
}}
$script:routes[$script:endpoint] = 'http://127.0.0.1:9999'
if (Undo-JarvisServeRoute -Endpoint $script:endpoint -HttpsPort 9443) {{
    throw 'Foreign route was removed'
}}
if (($script:events -join ',') -ne 'start,off,start') {{ throw 'Foreign route was changed' }}
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr
