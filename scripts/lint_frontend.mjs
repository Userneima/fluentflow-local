// Lint frontend source via project-level ESLint flat config, then look for
// code nothing uses (knip.json; product code only, tests do not count as a
// user). Errors fail the run; ESLint warnings are reported but do not.
//
// knip is here because removing a feature's entry point left its code behind:
// the web export buttons went on 2026-10-31 (decision 6.33) and ~800 lines of
// export code, two dependencies and duplicated request helpers stayed until
// 2026-11-19. When knip names something, delete it; if it is used in a way
// knip cannot see, say so in knip.json.
import { execSync } from 'node:child_process';

for (const command of ['npx eslint frontend/src/', 'npx knip --production --no-progress']) {
  try {
    execSync(command, { stdio: 'inherit', env: { ...process.env } });
  } catch (error) {
    process.exit(typeof error.status === 'number' ? error.status : 1);
  }
}
