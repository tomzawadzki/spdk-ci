// Simple test runner — validates the built plugin contains expected patterns
import { readFileSync } from 'fs';

const js = readFileSync('dist/spdk-checks.js', 'utf8');

let passed = 0;
let failed = 0;

function assert(condition, message) {
  if (!condition) {
    console.error(`FAIL: ${message}`);
    failed++;
  } else {
    console.log(`PASS: ${message}`);
    passed++;
  }
}

// Check the built file contains expected strings
assert(js.includes('Gerrit.install'), 'Plugin registers with Gerrit.install');
assert(js.includes('checks-api/v1'), 'Plugin calls the backend API');
assert(js.includes('RUNNABLE'), 'Plugin handles RUNNABLE status');
assert(js.includes('RUNNING'), 'Plugin handles RUNNING status');
assert(js.includes('COMPLETED'), 'Plugin handles COMPLETED status');
assert(js.includes('SCHEDULED'), 'Plugin handles SCHEDULED status');
assert(js.includes('Run CI'), 'Plugin has Run CI action');
assert(js.includes('Rerun Failed'), 'Plugin has Rerun Failed action');
assert(js.includes('Verified'), 'Plugin references Verified label');
assert(js.includes('BUILD'), 'Plugin has BUILD tag');
assert(js.includes('TEST'), 'Plugin has TEST tag');
assert(js.includes('LINT'), 'Plugin has LINT tag');
assert(js.includes('external'), 'Plugin uses external link icon');

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
console.log('All frontend tests passed!');
