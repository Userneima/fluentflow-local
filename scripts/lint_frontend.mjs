// Lint frontend source via project-level ESLint flat config.
// Errors fail the run; warnings are reported but do not.
import { execSync } from 'node:child_process';

try {
  execSync('npx eslint frontend/src/', { stdio: 'inherit', env: { ...process.env } });
} catch (error) {
  process.exit(typeof error.status === 'number' ? error.status : 1);
}
