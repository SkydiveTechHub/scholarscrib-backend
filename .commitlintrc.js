module.exports = {
    extends: ['@commitlint/config-conventional'],
    rules: {
        'scope-case': [2, 'always', 'lower-case'],
        'type-case': [2, 'always', 'lower-case'],
        'subject-case': [1, 'always', ['sentence-case', 'lower-case']],
    },
};
