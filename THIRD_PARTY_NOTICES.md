# Third-Party Notices

This file records the third-party material adapted into the SINCOR
skill catalogue, together with the licence text for each source.

---

## obra/superpowers (MIT)

Source repository: https://github.com/obra/superpowers

The SINCOR AgentSkill catalogue entries listed below are adapted from
the skill procedures in the obra/superpowers repository (skills/
directory): each procedure was re-expressed in SINCOR's AgentSkill
format (name, description, tags, examples, input/output schemas,
pricing, latency). Two skills were renamed for SINCOR branding; the
mapping is recorded next to each entry.

Upstream skill -> SINCOR catalogue id:

- verification-before-completion -> verification-before-completion
- test-driven-development -> test-driven-development
- systematic-debugging -> systematic-debugging
- requesting-code-review -> requesting-code-review
- receiving-code-review -> receiving-code-review
- subagent-driven-development -> subagent-driven-development
- dispatching-parallel-agents -> dispatching-parallel-agents
- writing-plans -> writing-plans
- executing-plans -> executing-plans
- using-git-worktrees -> using-git-worktrees
- writing-skills -> writing-skills
- brainstorming -> brainstorming
- finishing-a-development-branch -> finishing-a-development-branch
- diagnosing-superpowers -> agent-methodology-self-check (renamed)
- using-superpowers -> agent-methodology-guide (renamed)

### MIT License

MIT License

Copyright (c) 2025 Jesse Vincent

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

## ComposioHQ/awesome-claude-skills (Apache-2.0)

The following skills were adapted into the SINCOR agent-skill catalogue
(`src/sincor2/a2a_integration.py`) from the ComposioHQ
**awesome-claude-skills** repository:

- Source repository: https://github.com/ComposioHQ/awesome-claude-skills
- Default branch at time of adoption: `master`
- License: Apache License, Version 2.0 (http://www.apache.org/licenses/LICENSE-2.0)

Each skill below was verified at adoption time (2026-11-06) to carry its
own Apache-2.0 `LICENSE.txt` inside its upstream skill folder. The
SINCOR catalogue entries are adaptations re-expressed in SINCOR's
AgentSkill format (name, description, tags, examples, schemas, pricing);
they are not verbatim copies of the upstream files.

### Apache License 2.0 notice

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

### Adopted skills and upstream source paths

| SINCOR skill id | Upstream SKILL.md | Upstream LICENSE.txt |
| --- | --- | --- |
| webapp-testing | `webapp-testing/SKILL.md` | `webapp-testing/LICENSE.txt` |
| mcp-builder | `mcp-builder/SKILL.md` | `mcp-builder/LICENSE.txt` |
| skill-creator | `skill-creator/SKILL.md` | `skill-creator/LICENSE.txt` |
| internal-comms | `internal-comms/SKILL.md` | `internal-comms/LICENSE.txt` |
| brand-guidelines | `brand-guidelines/SKILL.md` | `brand-guidelines/LICENSE.txt` |
| canvas-design | `canvas-design/SKILL.md` | `canvas-design/LICENSE.txt` |
| theme-factory | `theme-factory/SKILL.md` | `theme-factory/LICENSE.txt` |
| artifacts-builder | `artifacts-builder/SKILL.md` | `artifacts-builder/LICENSE.txt` |
| slack-gif-creator | `slack-gif-creator/SKILL.md` | `slack-gif-creator/LICENSE.txt` |

Full upstream URLs follow the pattern
`https://github.com/ComposioHQ/awesome-claude-skills/blob/master/<path>`
(equivalently, raw content at
`https://raw.githubusercontent.com/ComposioHQ/awesome-claude-skills/master/<path>`).
