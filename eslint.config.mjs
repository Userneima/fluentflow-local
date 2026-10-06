import js from '@eslint/js';
import globals from 'globals';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';

export default [
  js.configs.recommended,
  {
    files: ['frontend/src/**/*.{js,jsx}'],
    plugins: {react, 'react-hooks': reactHooks},
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      parserOptions: {ecmaFeatures: {jsx: true}},
      globals: {...globals.browser, ...globals.node},
    },
    settings: {react: {version: 'detect'}},
    rules: {
      // JSX identifiers count as used; without these every imported component
      // and the React namespace show up as unused.
      'react/jsx-uses-vars': 'error',
      'react/jsx-uses-react': 'error',
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      'no-unused-vars': ['error', {
        args: 'after-used',
        argsIgnorePattern: '^_',
        caughtErrors: 'none',
        varsIgnorePattern: '^_',
        destructuredArrayIgnorePattern: '^_',
      }],
      'no-empty': ['error', {allowEmptyCatch: true}],
    },
  },
];
