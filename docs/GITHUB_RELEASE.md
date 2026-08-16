# GitHub Release Checklist

Use a private repository until you have reviewed every file.

## Preflight

```sh
git status --short
python3 -m unittest discover -s tests -p 'test_*.py'
./scripts/setup.sh --dry-run --owner-name "Example Owner" --vault-title "Example Brain" --yes
```

Check for local paths and private state:

```sh
rg -n "/Users/|moustafasabra|config/paths.local|runtime.local|data/raw|data/state" .
```

Expected local-only files are ignored by `.gitignore`.

## Create A Private Repo

```sh
gh repo create personal-brain-engine --private --source=. --remote=origin --push
```

If the repository already exists:

```sh
git remote add origin git@github.com:<owner>/personal-brain-engine.git
git push -u origin main
```
