// Lint frontend source via project-level ESLint flat config.
import { execSync } from 'node:child_process';

execSync(`npx eslint frontend/src/`, { stdio: 'inherit', env: { ...process.env } });
