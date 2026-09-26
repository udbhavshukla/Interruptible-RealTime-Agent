# AGENTS.md

## Repository state

- This checkout is a documentation-only scaffold: aside from this instruction file, it contains only `README.md` and `.gitignore`; there is no source tree, package/dependency manifest, lockfile, test suite, CI workflow, or repository-local OpenCode config.
- `.gitignore` is a broad Python template, not a project toolchain declaration. Do not assume Python, a package manager, or a runnable entrypoint until executable project files are added.
- `README.md` describes the intended product scope, but does not provide setup, run, or verification commands.

## Working rules

- Re-check the root and its config whenever the repository changes; prefer manifests, scripts, and other executable sources over assumptions or this file.
- Do not invent architecture, dependencies, or operational claims. When adding the first runnable implementation, document only commands that can be demonstrated and keep the README aligned with the code.
