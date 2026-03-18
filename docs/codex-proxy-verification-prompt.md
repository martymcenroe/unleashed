# Codex Proxy Verification Prompt

Paste everything below the line into a fresh `unleashed-t-alpha` session.

---

You are running inside Codex CLI, wrapped by `unleashed-t-03.py`. Your sandbox shell is PowerShell, not bash. This wrapper sets env vars before spawning Codex to work around sandbox restrictions:

1. `NO_PROXY=*` — bypasses the sandbox's proxy vars (`HTTP_PROXY`, `HTTPS_PROXY`, etc. set to `http://127.0.0.1:9`) that block all HTTP traffic
2. `GIT_CONFIG_COUNT=1`, `GIT_CONFIG_KEY_0=http.sslBackend`, `GIT_CONFIG_VALUE_0=openssl` — forces git to use OpenSSL instead of Windows schannel, because the sandbox strips credential store access that schannel needs

Your job is to verify these fixes work. Do NOT modify any files. Just run the checks and report results.

## Step 1: Check proxy environment

```powershell
Get-ChildItem Env: | Where-Object { $_.Name -match 'proxy|GIT_CONFIG' } | Sort-Object Name | ForEach-Object { "{0}={1}" -f $_.Name, $_.Value }
```

Expected: `NO_PROXY=*` is present. `GIT_CONFIG_COUNT=1`, `GIT_CONFIG_KEY_0=http.sslBackend`, `GIT_CONFIG_VALUE_0=openssl` are present. You will also see the sandbox proxy vars — that is expected and fine.

## Step 2: Test gh CLI

```powershell
gh api "repos/octocat/Hello-World/issues?per_page=1" --jq ".[0].number"
```

Expected: a number.

```powershell
gh issue list --repo octocat/Hello-World --limit 3
```

Expected: three issue rows.

## Step 3: Test git

```powershell
git ls-remote https://github.com/octocat/Hello-World.git HEAD
```

Expected: a commit hash followed by `HEAD`.

## Reporting

After all three steps, give me a summary table:

| Check | Result | Notes |
|-------|--------|-------|
| NO_PROXY=* present | PASS/FAIL | |
| GIT_CONFIG vars present | PASS/FAIL | |
| gh api | PASS/FAIL | |
| gh issue list | PASS/FAIL | |
| git ls-remote | PASS/FAIL | |

Do NOT create, modify, or delete any files. This is a read-only verification session.
