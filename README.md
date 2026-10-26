# Tensor9 skills

BYOC onboarding skills for Codex and Claude Code. They inspect local Terraform, explain integration choices, and write vendor-approved annotations and separately reviewed configuration changes or repairs. Skill work stays local; compiling, publishing, and deploying are separate actions.

Native qualification is **in progress**. Planned qualification uses isolated cloud-build cleanroom environments, installs these public packages, and exercises actual native agents, including a missing-CLI installation case. No native case is reported as passed here. Local package preparation and static checks establish setup evidence only.

Clone this repository, then copy the complete folder for your agent into your Terraform project's skill directory. Keep `references/` and `scripts/` with `SKILL.md`. If a destination already exists, review its contents before replacing it.

```sh
git clone https://github.com/tensor9ine/tensor9-skills.git
```

From your Terraform project, set the absolute path to that clone. For Codex:

```sh
TENSOR9_SKILLS_SOURCE="/absolute/path/to/tensor9-skills"
mkdir -p .agents/skills
cp -R "$TENSOR9_SKILLS_SOURCE/codex/skills/tensor9-byoc-onboarding" .agents/skills/
```

Invoke it in Codex:

```text
$tensor9-byoc-onboarding ./terraform
```

For Claude Code, copy its complete provider folder:

```sh
TENSOR9_SKILLS_SOURCE="/absolute/path/to/tensor9-skills"
mkdir -p .claude/skills
cp -R "$TENSOR9_SKILLS_SOURCE/claude/skills/tensor9-byoc-onboarding" .claude/skills/
```

Invoke it in Claude Code:

```text
/tensor9-byoc-onboarding ./terraform
```

Replace `./terraform` with your Terraform root. These locations and invocation forms follow the [Codex skill guide](https://learn.chatgpt.com/docs/build-skills) and [Claude Code skill guide](https://code.claude.com/docs/en/skills). The agent asks about vendor decisions and keeps unresolved choices open. Review actual annotations and any separate source repair diff before accepting the result. Keep credentials out of prompts and reports; source processing follows your coding agent's configured data handling.

If the public `tensor9` command is missing, read the bundled public CLI installation guide for [Codex](codex/skills/tensor9-byoc-onboarding/references/install-tensor9-cli.md) or [Claude Code](claude/skills/tensor9-byoc-onboarding/references/install-tensor9-cli.md). It explains supported methods and requires consent for the installation source and destination. Installing the CLI does not authorize logging in, changing profiles, compiling, or deploying.

The bundle includes public catalog **1.0.0** and a common Terraform mistakes reference. Python 3 enables its bundled catalog loader. The loader reads generic metadata through the installed public `tensor9` CLI and the vendor's selected VCtrl; it does not upload Terraform or vendor answers. It refreshes missing or older-than-24-hour cache data automatically. Use `--refresh` to force a metadata refresh or `--no-refresh` to use cache/bundle data offline. Missing CLI, authentication failures, unsupported responses, and timeouts retain usable fallback data. Report catalog version, digest, source, cache age, refresh result, and fallback reason; a cached or bundled response is not a fresh controller read. See each skill's `references/considerations-usage.md` for profile selection, cache behavior, and trust limits.

`public-source-manifest.json` records the upstream base revision, reviewed CLI-guide changes, and exact SHA-256 hashes of all eight resources per provider. It identifies packaged source without claiming the upstream overlay is already published or qualified. No license has been selected for this repository.
