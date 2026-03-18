# Gemini Shell & Sandbox Detection Prompt

Paste everything below the line into a Gemini CLI session.

---

I need you to run a series of diagnostic commands to tell me about your execution environment. Do NOT modify any files. Just run each command, show me the output, and summarize what you find.

## 1. What shell are you running in?

Run each of these. Some will error depending on your shell — that is expected and useful. Report which ones work and which error.

```
echo $SHELL
echo $BASH_VERSION
$PSVersionTable
cmd /c echo %COMSPEC%
```

## 2. What OS/platform do you see?

```
uname -a
[System.Environment]::OSVersion
```

## 3. Proxy environment

```
env | grep -i proxy
Get-ChildItem Env: | Where-Object { $_.Name -match 'proxy' } | Sort-Object Name | ForEach-Object { "{0}={1}" -f $_.Name, $_.Value }
```

Again, one of these will error — that tells us which shell you are in.

## 4. Network connectivity

```
gh api repos/octocat/Hello-World/issues?per_page=1 --jq ".[0].number"
git ls-remote https://github.com/octocat/Hello-World.git HEAD
curl -s -o /dev/null -w "%{http_code}" https://api.github.com
```

## 5. Git TLS backend

```
git config --show-origin --get http.sslbackend
git -c http.sslbackend=openssl ls-remote https://github.com/octocat/Hello-World.git HEAD
```

## 6. Environment dump (filtered)

Show me all env vars with names containing: PATH, GIT, PROXY, TERM, SHELL, COMSPEC, PSModulePath, GEMINI, GOOGLE, API.

Bash version:
```
env | grep -iE 'PATH|GIT|PROXY|TERM|SHELL|COMSPEC|GEMINI|GOOGLE|API' | sort
```

PowerShell version:
```
Get-ChildItem Env: | Where-Object { $_.Name -match 'PATH|GIT|PROXY|TERM|SHELL|COMSPEC|GEMINI|GOOGLE|API' } | Sort-Object Name | ForEach-Object { "{0}={1}" -f $_.Name, $_.Value }
```

## 7. Working directory and permissions

```
pwd
whoami
```

Try creating and deleting a temp file to test write access:
```
echo "test" > __gemini_sandbox_test.tmp
cat __gemini_sandbox_test.tmp
rm __gemini_sandbox_test.tmp
```

If any of those fail, report the exact error.

## Summary

After running everything, give me a table:

| Question | Answer |
|----------|--------|
| Shell type | bash / PowerShell / cmd / other |
| Shell version | |
| OS seen | |
| Proxy vars present | yes/no — list them |
| NO_PROXY set | yes/no |
| gh works | yes/no |
| git ls-remote works | yes/no |
| git ssl backend | schannel / openssl / not set |
| curl works | yes/no |
| Can write files | yes/no |
| Network blocked | yes/no — explain |
| Anything unexpected | describe |

Do NOT modify any project files. The temp file test above is the only write you should attempt, and delete it immediately after.
