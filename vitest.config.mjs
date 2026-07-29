import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// Node is the default environment (pure logic modules under frontend/src/lib).
// Component/provider tests opt into jsdom per-file via a
// `// @vitest-environment jsdom` docblock. See
// docs/task_list_reconciliation_plan.md.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'node',
    include: ['frontend/src/**/*.test.{js,jsx}'],
  },
});
