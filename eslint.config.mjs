import globals from 'globals';

export default [
  {
    ignores: [
      'frontend/dist/**',
      'frontend/dist-local/**',
      'node_modules/**',
    ],
  },
  {
    files: ['frontend/src/**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    rules: {
      'no-constant-binary-expression': 'error',
      'no-dupe-keys': 'error',
      'no-undef': 'error',
      'no-unreachable': 'error',
      // A result payload is either the record or a list row carrying previews,
      // and they are not interchangeable. Reading `summary_markdown` off
      // whichever one you happen to hold is how a 240-character preview reached
      // the note editor and its autosave overwrote a 13k-character note.
      // `lib/resultViews.js` owns these names and exposes the read split
      // (`noteForEditing` returns null for a preview, so a preview cannot be
      // saved). Everything else goes through it.
      'no-restricted-properties': ['error',
        { property: 'summary_markdown', message: 'Use noteForEditing/noteForDisplay from lib/resultViews.js.' },
        { property: 'transcript_text', message: 'Use transcriptForEditing/transcriptForDisplay from lib/resultViews.js.' },
        { property: 'summary_preview', message: 'Use noteForDisplay from lib/resultViews.js.' },
        { property: 'transcript_text_preview', message: 'Use transcriptForDisplay from lib/resultViews.js.' },
        { property: 'summary_markdown_chars', message: 'Use noteLength from lib/resultViews.js.' },
        { property: 'transcript_text_chars', message: 'Use transcriptLength from lib/resultViews.js.' },
      ],
    },
  },
  {
    // The owning module, and the tests that pin its behaviour, name the fields
    // directly — that is their job.
    files: ['frontend/src/lib/resultViews.js', 'frontend/src/**/*.test.{js,jsx}'],
    rules: { 'no-restricted-properties': 'off' },
  },
];
