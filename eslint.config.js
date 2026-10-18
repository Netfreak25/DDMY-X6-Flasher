import js from '@eslint/js';

export default [
  { ignores: ['vendor/**', 'node_modules/**', 'private_dumps/**'] },
  js.configs.recommended,
  { files: ['app.js', 'lib/**/*.js', 'web-tests/**/*.js'], languageOptions: { globals: {
    window: 'readonly', document: 'readonly', navigator: 'readonly', location: 'readonly',
    history: 'readonly', sessionStorage: 'readonly', crypto: 'readonly', fetch: 'readonly',
    URL: 'readonly', URLSearchParams: 'readonly', Blob: 'readonly', TextEncoder: 'readonly', TextDecoder: 'readonly',
    setTimeout: 'readonly', clearTimeout: 'readonly', console: 'readonly', process: 'readonly',
    AbortController: 'readonly', performance: 'readonly',
  } } },
];
